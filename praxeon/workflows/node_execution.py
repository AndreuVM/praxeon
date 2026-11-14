"""Ejecución de un nodo de workflow como pipeline de pasos (auditoría A-01).

``WorkflowEngine.execute_node`` recorre ``NODE_STEPS`` con el contrato común de ``praxeon.core.pipeline``:

1. ``PrepareNodeStep``: checkpoint previo, estado RUNNING y entradas resueltas.
2. ``RunNodeStep``: despacha al ejecutor de su tipo de nodo (``NODE_EXECUTORS``). Un nodo que queda a la espera (de un
   agente, de una aprobación humana o de la confirmación de una herramienta) cierra la pasada con ``Outcome.END``.
3. ``RecordNodeResultStep``: completa el nodo y propaga sus salidas, o aplica la política de reintentos.

El valor que devuelve ``execute_node`` queda en ``NodeRun.result``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional
import uuid

from praxeon.agents.protocol import AgentMessage, MessagePriority, MessageType
from praxeon.core.pipeline import Outcome, Step, run_steps
from praxeon.workflows.models import NodeStatus, NodeType, WorkflowNode, WorkflowStatus
from praxeon.workflows.semantics import EvaluationContext, parse_condition
from praxeon.workflows.supervised import WorkflowApprovalRequired

if TYPE_CHECKING:
    from praxeon.workflows.engine import WorkflowEngine


@dataclass
class NodeRun:
    """Un nodo en ejecución: sus entradas, su salida y lo que ``execute_node`` devolverá."""

    engine: "WorkflowEngine"
    node_id: str
    node: WorkflowNode
    now: datetime
    resolved_inputs: Dict[str, Any] = field(default_factory=dict)
    output: Dict[str, Any] = field(default_factory=dict)
    success: bool = True
    result: bool = False

    @property
    def context(self) -> Any:
        return self.engine.context

    @property
    def control_config(self) -> Dict[str, Any]:
        return self.node.control_config or self.node.metadata.get("control_config", {})

    def park(self, status: NodeStatus, output: Dict[str, Any]) -> None:
        """Deja el nodo a la espera con la salida dada (no se completa ni cuenta como fallo)."""
        with self.engine._lock:
            self.context.node_states[self.node_id] = status
            self.context.node_outputs[self.node_id] = output


def _merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    from praxeon.workflows.engine import _deep_merge
    return _deep_merge(base, extra)


# ------------------------------------------------------------------------------------------------ ejecutores por tipo
# Cada ejecutor devuelve la salida del nodo, o None si el nodo quedó a la espera (con su estado ya fijado).

NodeExecutor = Callable[[NodeRun], Optional[Dict[str, Any]]]


def _run_start(run: NodeRun) -> Optional[Dict[str, Any]]:
    return {"status": "started", "timestamp": datetime.now(timezone.utc).isoformat()}


def _run_end(run: NodeRun) -> Optional[Dict[str, Any]]:
    return {"status": "finished", "timestamp": datetime.now(timezone.utc).isoformat()}


def _run_task(run: NodeRun) -> Optional[Dict[str, Any]]:
    """Tarea: handler registrado, herramienta supervisada (PolicyEngine -> capability -> SecureExecutor -> sandbox),
    ejecutor de nodos o, solo si se permite expresamente, un resultado sintético."""
    engine, node, inputs = run.engine, run.node, run.resolved_inputs
    action_attr = getattr(node, "action", None)
    handlers = engine._handlers
    handler = (
        (action_attr and handlers.get(action_attr))
        or (node.tool_name and handlers.get(node.tool_name))
        or handlers.get(node.name)
        or handlers.get(node.node_id)
    )
    handler_key = action_attr or node.tool_name or node.name or node.node_id
    if handler:
        return handler(inputs)
    if engine.supervised_runner and node.tool_name:
        return engine.supervised_runner.run(
            execution_id=run.context.execution_id,
            workflow_id=engine.workflow.workflow_id,
            node_id=run.node_id,
            tool_name=node.tool_name,
            arguments={**node.inputs, **{k: v for k, v in inputs.items() if k in ("command", "cmd", "path", "content")}},
        )
    if engine.node_runner is not None and not node.tool_name:
        return engine.node_runner(node, inputs, engine)
    if engine.allow_synthetic_fallback:
        return {"task_result": f"Executed {handler_key}", "inputs_echo": inputs, "status": "completed", "simulated": True}
    raise NotImplementedError(
        f"No execution handler or ToolSpec registered for task '{handler_key}' in node '{run.node_id}'. "
        "Governed runtime requires real execution handlers or explicit mock injection."
    )


def _dispatch_to_agent(run: NodeRun, bus: Any, agent_id: str, prefix: str, waiting_output: Dict[str, Any]) -> None:
    """Envía la tarea al buzón del agente y deja el nodo esperando su respuesta asíncrona."""
    msg = AgentMessage(
        message_id=f"{prefix}_{run.node_id}_{uuid.uuid4().hex[:8]}",
        sender_id="praxeon_supervisor",
        receiver_id=agent_id,
        session_id=run.context.execution_id,
        task_id=run.node_id,
        message_type=MessageType.DELEGATE,
        priority=MessagePriority.HIGH,
        payload=run.resolved_inputs,
    )
    bus.send(msg)
    run.park(NodeStatus.WAITING_RESULT, {**waiting_output, "message_id": msg.message_id, "status": "waiting_result"})


def _run_agent(run: NodeRun) -> Optional[Dict[str, Any]]:
    engine, node = run.engine, run.node
    if engine.node_runner is not None:
        return engine.node_runner(node, run.resolved_inputs, engine)
    if engine.agent_bus and node.agent_id:
        _dispatch_to_agent(run, engine.agent_bus, node.agent_id, "wf_agent", {"agent_id": node.agent_id, "dispatched": True})
        return None
    if node.agent_id and node.agent_id in engine._handlers:
        return engine._handlers[node.agent_id](run.resolved_inputs)
    if engine.allow_synthetic_fallback:
        return {
            "agent_id": node.agent_id or "unassigned",
            "response": f"Agent {node.agent_id} completed goal",
            "status": "completed",
            "simulated": True,
        }
    raise NotImplementedError(
        f"No AgentMessageBus or execution handler configured for agent '{node.agent_id}' in node '{run.node_id}'."
    )


def _run_decision(run: NodeRun) -> Optional[Dict[str, Any]]:
    """DECISION / IF: evalúa la condición de control_config o, sin ella, la primera arista saliente que se cumpla."""
    engine, node_id = run.engine, run.node_id
    ctrl = run.node.control_config or run.node.metadata.get("control_config")
    eval_ctx = engine.get_evaluation_context(upstream_node_id=node_id)
    matched = False
    branch_taken = None
    reason = "Decisión evaluada"

    if ctrl and ctrl.get("condition") is not None:
        parsed_cond = parse_condition(ctrl.get("condition"))
        if parsed_cond:
            try:
                matched = parsed_cond.evaluate(eval_ctx)
                reason = f"Condición evaluada a {matched}"
            except Exception as exc:
                matched = False
                reason = f"Error en evaluación de condición: {str(exc)}"
                if ctrl.get("on_error"):
                    branch_taken = ctrl.get("on_error")
        if branch_taken is None:
            branch_taken = (ctrl.get("true_edge") or "true") if matched else (ctrl.get("false_edge") or ctrl.get("default_edge") or "false")
    else:
        outgoing = engine.workflow.get_outgoing_edges(node_id)
        for edge in outgoing:
            if edge.condition:
                legacy_ctx = {**engine.context.variables, **run.resolved_inputs}
                if edge.condition.evaluate(eval_ctx) or edge.condition.evaluate(legacy_ctx):
                    matched = True
                    branch_taken = edge.label or edge.edge_id
                    reason = f"Arista '{edge.edge_id}' satisfecha"
                    break
        if not branch_taken and outgoing:
            branch_taken = "default"

    return {
        "evaluated_at": run.now.isoformat(),
        "status": "evaluated",
        "matched": matched,
        "branch": "true" if matched else "false",
        "branch_taken": branch_taken,
        "reason": reason,
    }


def _run_while(run: NodeRun) -> Optional[Dict[str, Any]]:
    """WHILE: repite el cuerpo mientras se cumpla la condición; al agotar el límite aborta, escala o sale (on_limit).

    El límite solo actúa si la condición pide seguir iterando: un bucle cuya condición ya es falsa termina de forma
    natural aunque haya agotado sus iteraciones.
    """
    engine, node, node_id = run.engine, run.node, run.node_id
    with engine._lock:
        ctrl = run.control_config
        max_iter = ctrl.get("max_iterations") or node.metadata.get("max_iterations", 10)
        on_limit = (ctrl.get("on_limit") or "ABORT").upper()
        body_entry = ctrl.get("body_entry")
        exit_target = ctrl.get("exit_target")
        variables = engine.context.variables

        loops_state = variables.setdefault("_loops", {})
        current_loop = loops_state.setdefault(node_id, {"iteration": 0, "started_at": run.now.isoformat()})
        current_iter = current_loop["iteration"]
        variables["_active_loop"] = {"node_id": node_id, "iteration": current_iter, "max_iterations": max_iter}

        eval_ctx = engine.get_evaluation_context(upstream_node_id=node_id)
        condition_spec = ctrl.get("condition")
        if condition_spec is not None:
            parsed_cond = parse_condition(condition_spec)
            loop_matched = parsed_cond.evaluate(eval_ctx) if parsed_cond else False
        else:
            loop_matched = True   # sin condición, el bucle solo termina al agotar el límite (acción on_limit)

        if current_iter >= max_iter and loop_matched:
            if on_limit == "ABORT":
                raise RuntimeError(f"Límite de iteraciones alcanzado en bucle WHILE '{node_id}' ({max_iter} iteraciones).")
            if on_limit == "ESCALATE":
                run.park(NodeStatus.WAITING_APPROVAL, {
                    "status": "escalated_on_limit",
                    "iteration": current_iter,
                    "max_iterations": max_iter,
                    "branch_taken": "escalate",
                })
                return None
            loop_matched = False

        if loop_matched:
            current_loop["iteration"] = current_iter + 1
            variables["_active_loop"]["iteration"] = current_iter + 1
            # Resetear nodos del cuerpo del bucle para la nueva iteración
            engine._reset_loop_body_nodes(node_id, exit_target=exit_target)
            return {
                "iteration": current_iter + 1,
                "max_iterations": max_iter,
                "condition_matched": True,
                "branch_taken": body_entry or "body",
                "action": "repeat_body",
                "decision": "CONTINUE",
                "status": "looping",
            }
        variables.pop("_active_loop", None)
        return {
            "iteration": current_iter,
            "max_iterations": max_iter,
            "condition_matched": False,
            "branch_taken": exit_target or "exit",
            "action": "exit_loop",
            "decision": "EXIT",
            "status": "completed",
        }


def _run_delegate(run: NodeRun) -> Optional[Dict[str, Any]]:
    """DELEGATE: asigna la tarea a un agente (fijo o, en modo AUTOMATIC, el primer candidato)."""
    engine, node_id = run.engine, run.node_id
    ctrl = run.control_config
    routing_mode = (ctrl.get("routing_mode") or "MANUAL").upper()
    target_agent = run.node.agent_id or ctrl.get("target_agent_id")
    if routing_mode == "AUTOMATIC":
        candidates = ctrl.get("candidate_agents") or []
        if candidates:
            target_agent = candidates[0]
    if not target_agent:
        raise ValueError(f"Nodo DELEGATE '{node_id}' no tiene agente asignado ni candidatos válidos.")

    if engine.node_runner is not None:
        output = engine.node_runner(run.node, {**run.resolved_inputs, "_target_agent_id": target_agent}, engine)
        output["delegated_to"] = target_agent
        return output
    if engine.agent_bus:
        _dispatch_to_agent(run, engine.agent_bus, target_agent, "wf_delegate", {"delegated_to": target_agent, "routing_mode": routing_mode})
        return None
    if target_agent in engine._handlers:
        output = engine._handlers[target_agent](run.resolved_inputs)
        output["delegated_to"] = target_agent
        return output
    if engine.allow_synthetic_fallback:
        return {
            "delegated_to": target_agent,
            "routing_mode": routing_mode,
            "status": "completed",
            "response": f"Agent {target_agent} completed delegated task",
            "simulated": True,
        }
    raise NotImplementedError(f"No handler or bus configured for delegated agent '{target_agent}' in node '{node_id}'.")


def _run_human_approval(run: NodeRun) -> Optional[Dict[str, Any]]:
    ctrl = run.control_config
    run.park(NodeStatus.WAITING_APPROVAL, {
        "prompt": ctrl.get("prompt") or run.node.inputs.get("prompt") or "Aprobación requerida",
        "approver_role": ctrl.get("approver_role") or "supervisor",
        "status": "waiting_approval",
        "requested_at": run.now.isoformat(),
    })
    return None


def _run_parallel_join(run: NodeRun) -> Optional[Dict[str, Any]]:
    """PARALLEL_JOIN: fusiona las salidas de las ramas (shallow, namespace o reduce) y, si se pide, cancela las que sigan."""
    engine, ctrl = run.engine, run.control_config
    merge_policy = ctrl.get("merge_policy", "shallow")
    incoming_edges = engine.workflow.get_incoming_edges(run.node_id)
    if ctrl.get("cancel_remaining", False):
        with engine._lock:
            for edge in incoming_edges:
                if engine.context.node_states.get(edge.from_node) in (NodeStatus.RUNNING, NodeStatus.PENDING, NodeStatus.READY):
                    engine.context.node_states[edge.from_node] = NodeStatus.CANCELLED

    outputs = engine.context.node_outputs
    merged: Dict[str, Any] = {"parallel_sync": True, "status": "completed"}
    for edge in incoming_edges:
        upstream = outputs.get(edge.from_node, {})
        if merge_policy == "namespace":
            merged[edge.from_node] = upstream
        elif merge_policy == "reduce":
            merged = _merge(merged, upstream)
        else:
            merged.update(upstream)
    return merged


def _run_parallel_fork(run: NodeRun) -> Optional[Dict[str, Any]]:
    return {"parallel_sync": True, "status": "completed"}


NODE_EXECUTORS: Dict[NodeType, NodeExecutor] = {
    NodeType.START: _run_start,
    NodeType.END: _run_end,
    NodeType.TASK: _run_task,
    NodeType.AGENT: _run_agent,
    NodeType.DECISION: _run_decision,
    NodeType.IF: _run_decision,
    NodeType.WHILE: _run_while,
    NodeType.DELEGATE: _run_delegate,
    NodeType.HUMAN_APPROVAL: _run_human_approval,
    NodeType.PARALLEL_JOIN: _run_parallel_join,
    NodeType.PARALLEL_FORK: _run_parallel_fork,
}


# ------------------------------------------------------------------------------------------------ pasos


class PrepareNodeStep(Step[NodeRun]):
    """Checkpoint antes de mutar el nodo, estado RUNNING y entradas: variables globales, salidas de los predecesores
    (también como ``upstream_<id>``), ``inputs`` propios e ``input_mapping``."""

    name = "prepare_node"

    def run(self, run: NodeRun) -> Outcome:
        engine, node, node_id = run.engine, run.node, run.node_id
        with engine._lock:
            context = engine.context
            context.create_checkpoint(current_node_id=node_id)
            context.node_states[node_id] = NodeStatus.RUNNING
            context.node_started_at[node_id] = run.now

            inputs = dict(context.variables)
            for up_id in engine.workflow.get_upstream_node_ids(node_id):
                if up_id in context.node_outputs:
                    inputs[f"upstream_{up_id}"] = context.node_outputs[up_id]
                    inputs.update(context.node_outputs[up_id])
            inputs.update(node.inputs)
            if node.input_mapping:
                eval_ctx = engine.get_evaluation_context()
                for local_name, source_path in node.input_mapping.items():
                    inputs[local_name] = eval_ctx.resolve_field(source_path)
            run.resolved_inputs = inputs
        return Outcome.CONTINUE


class RunNodeStep(Step[NodeRun]):
    """Ejecuta el nodo según su tipo. Un error deja ``success=False`` para la política de reintentos."""

    name = "run_node"

    def run(self, run: NodeRun) -> Outcome:
        executor = NODE_EXECUTORS.get(run.node.node_type)
        try:
            output = executor(run) if executor is not None else {}
        except WorkflowApprovalRequired as exc:
            # La política exige confirmación humana: el nodo queda a la espera, sin ejecutarse
            run.park(NodeStatus.WAITING_APPROVAL, {
                "status": "waiting_approval",
                "awaiting_tool_confirmation": True,
                "action_id": exc.action_id,
                "tool": exc.tool_name,
                "reason_codes": exc.reason_codes,
                "requested_at": run.now.isoformat(),
            })
            run.result = True
            return Outcome.END
        except Exception as exc:
            run.success, run.output = False, {"error": str(exc)}
            return Outcome.CONTINUE
        if output is None:   # el nodo quedó a la espera
            run.result = True
            return Outcome.END
        run.output = output
        return Outcome.CONTINUE


class RecordNodeResultStep(Step[NodeRun]):
    """Completa el nodo y propaga su salida, o aplica la política de reintentos (con backoff) hasta marcarlo FAILED."""

    name = "record_node_result"

    def run(self, run: NodeRun) -> Outcome:
        engine = run.engine
        with engine._lock:
            if engine.context.status == WorkflowStatus.CANCELLED:
                run.result = False   # cancelado mientras el nodo se ejecutaba: el resultado se descarta
                return Outcome.END
            if run.success:
                self._complete(run)
                run.result = True
            else:
                self._retry_or_fail(run)
                run.result = False
        return Outcome.CONTINUE

    @staticmethod
    def _complete(run: NodeRun) -> None:
        engine, node, node_id, context = run.engine, run.node, run.node_id, run.context
        context.node_states[node_id] = NodeStatus.COMPLETED
        context.node_outputs[node_id] = run.output
        context.execution_history.append(node_id)
        context.node_retry_after.pop(node_id, None)
        if node.output_mapping:
            for var_name, out_path in node.output_mapping.items():
                context.variables[var_name] = EvaluationContext._navigate(run.output, out_path.split("."))

        # Si una arista saliente apunta a un bucle WHILE, reactivar el nodo WHILE para la siguiente iteración
        for out_edge in engine.workflow.get_outgoing_edges(node_id):
            target_node = engine.workflow.nodes[out_edge.to_node]
            if target_node.node_type == NodeType.WHILE and out_edge.to_node != node_id and node.node_type != NodeType.WHILE:
                context.node_states[out_edge.to_node] = NodeStatus.READY

        # Evaluar propagación y marcar ramas excluidas como SKIPPED
        engine._propagate_skips(node_id)

        # Si es END y se cumplen las condiciones de conclusión, finalizar el estado terminal
        if node.node_type == NodeType.END and engine._should_complete_workflow():
            engine._finalize_terminal_state()

    @staticmethod
    def _retry_or_fail(run: NodeRun) -> None:
        node, node_id, context = run.node, run.node_id, run.context
        retries = context.node_retries.get(node_id, 0)
        if retries < node.retry_policy.max_retries:
            context.node_retries[node_id] = retries + 1
            delay = node.retry_policy.delay_seconds * (node.retry_policy.backoff_multiplier ** retries)
            if delay > 0:
                context.node_states[node_id] = NodeStatus.RETRYING
                context.node_retry_after[node_id] = run.now + timedelta(seconds=delay)
            else:
                context.node_states[node_id] = NodeStatus.READY
            return
        context.node_states[node_id] = NodeStatus.FAILED
        context.node_outputs[node_id] = run.output
        context.status = WorkflowStatus.FAILED
        context.error_message = f"Fallo en nodo '{node_id}': {run.output.get('error')}"
        context.finished_at = run.now


NODE_STEPS: tuple[Step[NodeRun], ...] = (PrepareNodeStep(), RunNodeStep(), RecordNodeResultStep())


def execute_node(engine: "WorkflowEngine", node_id: str, current_time: Optional[datetime] = None) -> bool:
    """Ejecuta un nodo individual y devuelve si avanzó (completado o a la espera) o no (fallo, reintento, cancelación)."""
    now = current_time or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    run = NodeRun(engine=engine, node_id=node_id, node=engine.workflow.nodes[node_id], now=now)
    run_steps(NODE_STEPS, run)
    return run.result
