"""Motor y Máquina de Estados de Ejecución de Workflows con Backtrack Determinista (F6-02).

Implementa la orquestación y control de ejecución en tiempo real:
- WorkflowStatus: Ciclo de vida global de ejecución (IDLE, RUNNING, PAUSED, COMPLETED, FAILED, CANCELLED).
- ExecutionCheckpoint: Instantánea inmutable del estado del grafo para retroceso determinista (backtrack).
- WorkflowExecutionContext: Almacén reactivo de variables, resultados por nodo e historial.
- WorkflowEngine:
    * start(), step(), run_to_completion().
    * pause(), resume(), cancel().
    * retry_node() con políticas de backoff exponencial.
    * backtrack_to_node() para restauración atómica del grafo a un punto previo.
    * Despacho coordinado a tareas y agentes (integración opcional con AgentMessageBus).
"""

from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from enum import Enum
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid
from pydantic import BaseModel, ConfigDict, Field

from praxeon.agents.bus import AgentMessageBus
from praxeon.agents.protocol import AgentMessage, MessagePriority, MessageType
from praxeon.workflows.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowExecution,
    WorkflowNode,
    WorkflowStatus,
)
from praxeon.workflows.semantics import (
    AtomicCondition,
    CompoundCondition,
    ConditionResult,
    ControlConfig,
    EvaluationContext,
    parse_condition,
)



class ExecutionCheckpoint(BaseModel):
    """Punto de restauración inmutable del estado del grafo para retroceso determinista."""
    model_config = ConfigDict(frozen=True)

    checkpoint_id: str
    target_node_id: Optional[str] = None
    node_states: Dict[str, NodeStatus]
    node_outputs: Dict[str, Dict[str, Any]]
    variables: Dict[str, Any]
    execution_history: List[str]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WorkflowExecutionContext:
    """Contexto y estado mutable de una ejecución activa de workflow."""

    def __init__(self, workflow: WorkflowDefinition, execution_id: Optional[str] = None):
        self.execution_id = execution_id or f"exec_{uuid.uuid4().hex[:12]}"
        self.workflow_id = workflow.workflow_id
        self.status = WorkflowStatus.IDLE
        self.node_states: Dict[str, NodeStatus] = {nid: NodeStatus.PENDING for nid in workflow.nodes}
        self.node_outputs: Dict[str, Dict[str, Any]] = {}
        self.node_retries: Dict[str, int] = {nid: 0 for nid in workflow.nodes}
        self.node_started_at: Dict[str, datetime] = {}
        self.node_retry_after: Dict[str, datetime] = {}
        self.variables: Dict[str, Any] = dict(workflow.variables)
        self.execution_history: List[str] = []
        self.checkpoints: List[ExecutionCheckpoint] = []
        self.error_message: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None

    def create_checkpoint(self, current_node_id: Optional[str] = None) -> ExecutionCheckpoint:
        """Captura una instantánea del estado actual de ejecución."""
        ckpt = ExecutionCheckpoint(
            checkpoint_id=f"ckpt_{len(self.checkpoints)+1}_{uuid.uuid4().hex[:8]}",
            target_node_id=current_node_id,
            node_states=dict(self.node_states),
            node_outputs={nid: dict(out) for nid, out in self.node_outputs.items()},
            variables=dict(self.variables),
            execution_history=list(self.execution_history),
        )
        self.checkpoints.append(ckpt)
        return ckpt

    def restore_checkpoint(self, checkpoint: ExecutionCheckpoint) -> None:
        """Restaura el contexto de ejecución a partir de un checkpoint inmutable."""
        self.node_states = dict(checkpoint.node_states)
        self.node_outputs = {nid: dict(out) for nid, out in checkpoint.node_outputs.items()}
        self.variables = dict(checkpoint.variables)
        self.execution_history = list(checkpoint.execution_history)
        self.error_message = None

    def to_execution(self) -> WorkflowExecution:
        """Exporta el estado mutable del contexto a una entidad formal WorkflowExecution."""
        return WorkflowExecution(
            execution_id=self.execution_id,
            workflow_id=self.workflow_id,
            status=self.status,
            node_states=dict(self.node_states),
            node_outputs={k: dict(v) for k, v in self.node_outputs.items()},
            node_retries=dict(self.node_retries),
            node_started_at=dict(self.node_started_at),
            node_retry_after=dict(self.node_retry_after),
            variables=dict(self.variables),
            execution_history=list(self.execution_history),
            checkpoints=[ckpt.model_dump(mode="json") if hasattr(ckpt, "model_dump") else dict(ckpt) for ckpt in self.checkpoints],
            error_message=self.error_message,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )

    @classmethod
    def from_execution(cls, workflow: WorkflowDefinition, execution: WorkflowExecution) -> "WorkflowExecutionContext":
        """Reconstruye un contexto activo de ejecución a partir de una entidad WorkflowExecution."""
        ctx = cls(workflow=workflow, execution_id=execution.execution_id)
        ctx.status = execution.status
        ctx.node_states = dict(execution.node_states)
        ctx.node_outputs = {k: dict(v) for k, v in execution.node_outputs.items()}
        ctx.node_retries = dict(execution.node_retries)
        ctx.node_started_at = dict(execution.node_started_at)
        ctx.node_retry_after = dict(execution.node_retry_after)
        ctx.variables = dict(execution.variables)
        ctx.execution_history = list(execution.execution_history)
        ctx.error_message = execution.error_message
        ctx.started_at = execution.started_at
        ctx.finished_at = execution.finished_at
        return ctx

    def to_dict(self) -> Dict[str, Any]:
        """Serializa el estado del contexto de ejecución."""
        return {
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "status": self.status.value,
            "node_states": {k: v.value for k, v in self.node_states.items()},
            "node_outputs": self.node_outputs,
            "variables": self.variables,
            "node_retries": self.node_retries,
            "node_started_at": {k: v.isoformat() for k, v in self.node_started_at.items()},
            "node_retry_after": {k: v.isoformat() for k, v in self.node_retry_after.items()},
            "execution_history": self.execution_history,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class WorkflowEngine:
    """Motor orquestador de grafos de workflows con soporte para múltiples instancias de WorkflowExecution."""

    def __init__(
        self,
        workflow: WorkflowDefinition,
        agent_bus: Optional[AgentMessageBus] = None,
        task_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]] = None,
        tool_registry: Optional[Any] = None,
        allow_synthetic_fallback: bool = False,
        execution: Optional[WorkflowExecution] = None,
        execution_id: Optional[str] = None,
    ):
        self.workflow = workflow
        self.agent_bus = agent_bus
        self.tool_registry = tool_registry
        self.allow_synthetic_fallback = allow_synthetic_fallback
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = dict(task_handlers or {})
        self._lock = threading.RLock()

        if execution:
            self.context = WorkflowExecutionContext.from_execution(workflow=self.workflow, execution=execution)
        else:
            self.context = WorkflowExecutionContext(workflow=self.workflow, execution_id=execution_id)

        # Validar consistencia estructural del flujo
        errors = self.workflow.validate_graph()
        if errors:
            raise ValueError(f"No se puede instanciar WorkflowEngine con un grafo inválido: {'; '.join(errors)}")

    def get_execution(self) -> WorkflowExecution:
        """Retorna una instantánea formal e inmutable del estado de ejecución activo."""
        return self.context.to_execution()

    def create_new_execution(
        self,
        execution_id: Optional[str] = None,
        initial_variables: Optional[Dict[str, Any]] = None,
    ) -> WorkflowExecution:
        """Crea una nueva ejecución formal e independiente para este workflow sin alterar la actual."""
        return self.workflow.create_execution(
            execution_id=execution_id,
            initial_variables=initial_variables,
        )

    def bind_execution(self, execution: WorkflowExecution) -> None:
        """Vincula el motor a una ejecución específica, reemplazando el contexto activo."""
        if execution.workflow_id != self.workflow.workflow_id:
            raise ValueError(
                f"No se puede vincular una ejecución de '{execution.workflow_id}' "
                f"en un motor con workflow '{self.workflow.workflow_id}'"
            )
        self.context = WorkflowExecutionContext.from_execution(workflow=self.workflow, execution=execution)

    def register_task_handler(
        self,
        tool_or_action: str,
        handler: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> None:
        """Registra un ejecutor local para tareas o herramientas específicas."""
        self._handlers[tool_or_action] = handler

    def start(self, initial_variables: Optional[Dict[str, Any]] = None) -> WorkflowExecutionContext:
        """Inicia el ciclo de ejecución del flujo de trabajo."""
        if initial_variables:
            self.context.variables.update(initial_variables)

        start_node = self.workflow.get_start_node()
        if not start_node:
            raise ValueError("El workflow no contiene un nodo START válido.")

        self.context.status = WorkflowStatus.RUNNING
        self.context.started_at = datetime.now(timezone.utc)
        self.context.node_states[start_node.node_id] = NodeStatus.READY

        # Capturar checkpoint inicial
        self.context.create_checkpoint(current_node_id=start_node.node_id)
        return self.context

    def pause(self) -> None:
        """Pausa temporalmente el workflow."""
        if self.context.status == WorkflowStatus.RUNNING:
            self.context.status = WorkflowStatus.PAUSED

    def resume(self) -> WorkflowExecutionContext:
        """Reanuda la ejecución de un workflow pausado."""
        if self.context.status == WorkflowStatus.PAUSED:
            self.context.status = WorkflowStatus.RUNNING
        return self.context

    def cancel(self, reason: str = "User cancelled execution") -> None:
        """Cancela la ejecución de todo el workflow."""
        self.context.status = WorkflowStatus.CANCELLED
        self.context.error_message = reason
        self.context.finished_at = datetime.now(timezone.utc)
        for nid, st in self.context.node_states.items():
            if st in (NodeStatus.PENDING, NodeStatus.READY, NodeStatus.RUNNING):
                self.context.node_states[nid] = NodeStatus.CANCELLED

    def get_evaluation_context(self, upstream_node_id: Optional[str] = None) -> EvaluationContext:
        """Construye un EvaluationContext formal y estructurado con namespaces aislados."""
        last_res = self.context.node_outputs.get(upstream_node_id) if upstream_node_id else None
        if not last_res and self.context.execution_history:
            last_res = self.context.node_outputs.get(self.context.execution_history[-1])

        loop_info = dict(self.context.variables.get("_active_loop", {}))
        budget_info = dict(self.context.variables.get("_budget", {}))
        agent_state = dict(self.context.variables.get("_agent_state", {}))
        semantic_assessment = self.context.variables.get("_semantic_assessment")

        return EvaluationContext(
            variables=dict(self.context.variables),
            outputs={nid: dict(out) for nid, out in self.context.node_outputs.items()},
            last_result=last_res,
            loop=loop_info,
            budget=budget_info,
            agent_state=agent_state,
            semantic_assessment=semantic_assessment,
        )

    def is_node_ready(self, node_id: str) -> bool:
        """Evalúa si todas las dependencias entrantes de un nodo se han satisfecho."""
        node = self.workflow.nodes[node_id]
        if self.context.node_states[node_id] not in (NodeStatus.PENDING, NodeStatus.READY):
            return False

        # Invariante INV-05: El nodo terminal END no se considera listo mientras existan ramas activas requeridas
        if node.node_type == NodeType.END:
            active_nodes = [
                nid for nid, st in self.context.node_states.items()
                if st in (NodeStatus.RUNNING, NodeStatus.WAITING_RESULT, NodeStatus.WAITING_APPROVAL)
            ]
            if active_nodes:
                return False

        incoming_edges = self.workflow.get_incoming_edges(node_id)
        if not incoming_edges:
            # Si no tiene predecesores y es START, está listo
            return node.node_type == NodeType.START

        # Si el nodo es PARALLEL_JOIN, evaluar según política de join
        if node.node_type == NodeType.PARALLEL_JOIN:
            ctrl = node.control_config or node.metadata.get("control_config", {})
            join_policy = (ctrl.get("join_policy") or "all").lower()

            if join_policy == "all":
                for edge in incoming_edges:
                    up_st = self.context.node_states.get(edge.from_node)
                    if up_st not in (NodeStatus.COMPLETED, NodeStatus.SKIPPED):
                        return False
                return True
            elif join_policy == "any":
                return any(
                    self.context.node_states.get(edge.from_node) == NodeStatus.COMPLETED
                    for edge in incoming_edges
                )
            elif join_policy == "quorum":
                quorum_needed = ctrl.get("quorum_count") or (len(incoming_edges) // 2 + 1)
                completed_count = sum(
                    1 for edge in incoming_edges
                    if self.context.node_states.get(edge.from_node) == NodeStatus.COMPLETED
                )
                return completed_count >= quorum_needed

        # En nodos normales, DECISION, IF, WHILE: al menos una arista entrante debe estar satisfecha
        for edge in incoming_edges:
            up_st = self.context.node_states.get(edge.from_node)
            if up_st == NodeStatus.COMPLETED:
                upstream_node = self.workflow.nodes[edge.from_node]
                upstream_output = self.context.node_outputs.get(edge.from_node, {})

                # Si el predecesor fue un nodo de bifurcación condicional DECISION / IF
                if upstream_node.node_type in (NodeType.DECISION, NodeType.IF):
                    branch_taken = upstream_output.get("branch_taken")
                    if branch_taken is not None:
                        b_str = str(branch_taken).strip().lower()
                        edge_label = (edge.label or "").strip().lower()
                        if edge.edge_id == branch_taken or edge_label == b_str:
                            return True
                        if b_str == "true" and edge_label in ("true", "yes", "si", "1"):
                            return True
                        if b_str == "false" and edge_label in ("false", "no", "0"):
                            return True
                        if b_str == "default" and edge_label in ("default", "else"):
                            return True
                        # Si no coincide con la rama seleccionada pero tiene etiqueta explícita de rama, no satisface
                        if edge_label in ("true", "false", "yes", "no", "default", "else"):
                            continue

                if edge.condition is None:
                    return True

                # Evaluar condición de la arista con EvaluationContext y retrocompatibilidad
                eval_ctx = self.get_evaluation_context(upstream_node_id=edge.from_node)
                legacy_ctx = {
                    "output": upstream_output,
                    **self.context.variables,
                    **upstream_output,
                }
                if edge.condition.evaluate(eval_ctx) or edge.condition.evaluate(legacy_ctx):
                    return True

        return False

    def find_all_ready_nodes(self) -> List[str]:
        """Localiza todos los nodos actualmente listos para ejecutarse de manera independiente."""
        with self._lock:
            return [nid for nid in self.workflow.nodes if self.is_node_ready(nid)]

    def find_next_ready_node(self) -> Optional[str]:
        """Localiza el siguiente nodo listo para ejecutarse según el orden topológico."""
        with self._lock:
            ready = self.find_all_ready_nodes()
            return ready[0] if ready else None

    def execute_node(self, node_id: str, current_time: Optional[datetime] = None) -> bool:
        """Ejecuta un nodo individual, despacha tareas/agentes y propaga outputs."""
        node = self.workflow.nodes[node_id]

        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        with self._lock:
            # 1. Guardar checkpoint antes de la mutación del nodo
            self.context.create_checkpoint(current_node_id=node_id)

            self.context.node_states[node_id] = NodeStatus.RUNNING
            self.context.node_started_at[node_id] = now

            # 2. Construir inputs combinando variables globales y outputs de predecesores
            resolved_inputs = dict(self.context.variables)
            for up_id in self.workflow.get_upstream_node_ids(node_id):
                if up_id in self.context.node_outputs:
                    resolved_inputs[f"upstream_{up_id}"] = self.context.node_outputs[up_id]
                    resolved_inputs.update(self.context.node_outputs[up_id])
            resolved_inputs.update(node.inputs)

        output: Dict[str, Any] = {}
        success = True

        try:
            if node.node_type == NodeType.START:
                output = {"status": "started", "timestamp": datetime.now(timezone.utc).isoformat()}

            elif node.node_type == NodeType.END:
                output = {"status": "finished", "timestamp": datetime.now(timezone.utc).isoformat()}

            elif node.node_type == NodeType.TASK:
                handler_key = node.tool_name or node.name
                if handler_key in self._handlers:
                    output = self._handlers[handler_key](resolved_inputs)
                elif node.node_id in self._handlers:
                    output = self._handlers[node.node_id](resolved_inputs)
                elif self.tool_registry and node.tool_name and hasattr(self.tool_registry, "has_tool") and self.tool_registry.has_tool(node.tool_name):
                    tool_spec = self.tool_registry.get_tool(node.tool_name)
                    if hasattr(tool_spec, "handler") and callable(tool_spec.handler):
                        output = tool_spec.handler(resolved_inputs)
                    else:
                        output = {"tool": node.tool_name, "executed": True, "inputs": resolved_inputs}
                elif self.allow_synthetic_fallback:
                    output = {
                        "task_result": f"Executed {handler_key}",
                        "inputs_echo": resolved_inputs,
                        "status": "completed",
                    }
                else:
                    raise NotImplementedError(
                        f"No execution handler or ToolSpec registered for task '{handler_key}' in node '{node_id}'. "
                        "Governed runtime requires real execution handlers or explicit mock injection."
                    )

            elif node.node_type == NodeType.AGENT:
                if self.agent_bus and node.agent_id:
                    # Enviar mensaje formal al buzón del agente
                    msg = AgentMessage(
                        message_id=f"wf_agent_{node_id}_{uuid.uuid4().hex[:8]}",
                        sender_id="praxeon_supervisor",
                        receiver_id=node.agent_id,
                        session_id=self.context.execution_id,
                        task_id=node_id,
                        message_type=MessageType.DELEGATE,
                        priority=MessagePriority.HIGH,
                        payload=resolved_inputs,
                    )
                    self.agent_bus.send(msg)
                    # Poner el nodo en estado WAITING_RESULT esperando la respuesta asíncrona del agente
                    with self._lock:
                        self.context.node_states[node_id] = NodeStatus.WAITING_RESULT
                        self.context.node_outputs[node_id] = {
                            "agent_id": node.agent_id,
                            "dispatched": True,
                            "message_id": msg.message_id,
                            "status": "waiting_result",
                        }
                    return True
                elif node.agent_id and node.agent_id in self._handlers:
                    output = self._handlers[node.agent_id](resolved_inputs)
                elif self.allow_synthetic_fallback:
                    output = {
                        "agent_id": node.agent_id or "unassigned",
                        "response": f"Agent {node.agent_id} completed goal",
                        "status": "completed",
                    }
                else:
                    raise NotImplementedError(
                        f"No AgentMessageBus or execution handler configured for agent '{node.agent_id}' in node '{node_id}'."
                    )

            elif node.node_type in (NodeType.DECISION, NodeType.IF):
                ctrl = node.control_config or node.metadata.get("control_config")
                eval_ctx = self.get_evaluation_context(upstream_node_id=node_id)
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
                    else:
                        matched = False

                    if branch_taken is None:
                        if matched:
                            branch_taken = ctrl.get("true_edge") or "true"
                        else:
                            branch_taken = ctrl.get("false_edge") or "false"
                else:
                    # Si no hay condición explícita en control_config, evaluar aristas salientes
                    outgoing = self.workflow.get_outgoing_edges(node_id)
                    for edge in outgoing:
                        if edge.condition:
                            legacy_ctx = {**self.context.variables, **resolved_inputs}
                            if edge.condition.evaluate(eval_ctx) or edge.condition.evaluate(legacy_ctx):
                                matched = True
                                branch_taken = edge.label or edge.edge_id
                                reason = f"Arista '{edge.edge_id}' satisfecha"
                                break
                    if not branch_taken and outgoing:
                        branch_taken = "default"

                output = {
                    "evaluated_at": now.isoformat(),
                    "status": "evaluated",
                    "matched": matched,
                    "branch": "true" if matched else "false",
                    "branch_taken": branch_taken,
                    "reason": reason,
                }


            elif node.node_type == NodeType.WHILE:
                ctrl = node.control_config or node.metadata.get("control_config", {})
                max_iter = ctrl.get("max_iterations") or node.metadata.get("max_iterations", 10)
                on_limit = (ctrl.get("on_limit") or "ABORT").upper()
                body_entry = ctrl.get("body_entry")
                exit_target = ctrl.get("exit_target")

                loops_state = self.context.variables.setdefault("_loops", {})
                current_loop = loops_state.setdefault(node_id, {"iteration": 0, "started_at": now.isoformat()})
                current_iter = current_loop["iteration"]

                self.context.variables["_active_loop"] = {
                    "node_id": node_id,
                    "iteration": current_iter,
                    "max_iterations": max_iter,
                }

                eval_ctx = self.get_evaluation_context(upstream_node_id=node_id)
                condition_spec = ctrl.get("condition")
                loop_matched = True

                if condition_spec is not None:
                    parsed_cond = parse_condition(condition_spec)
                    if parsed_cond:
                        loop_matched = parsed_cond.evaluate(eval_ctx)
                else:
                    loop_matched = current_iter < max_iter

                if current_iter >= max_iter:
                    if on_limit == "ABORT":
                        raise RuntimeError(f"Límite de iteraciones alcanzado en bucle WHILE '{node_id}' ({max_iter} iteraciones).")
                    elif on_limit == "ESCALATE":
                        with self._lock:
                            self.context.node_states[node_id] = NodeStatus.WAITING_APPROVAL
                            self.context.node_outputs[node_id] = {
                                "status": "escalated_on_limit",
                                "iteration": current_iter,
                                "max_iterations": max_iter,
                                "branch_taken": "escalate",
                            }
                        return True
                    else:
                        loop_matched = False

                if loop_matched:
                    current_loop["iteration"] = current_iter + 1
                    self.context.variables["_active_loop"]["iteration"] = current_iter + 1
                    branch_taken = body_entry or "body"

                    # Resetear nodos del cuerpo del bucle para la nueva iteración
                    self._reset_loop_body_nodes(node_id, exit_target=exit_target)

                    output = {
                        "iteration": current_iter + 1,
                        "max_iterations": max_iter,
                        "condition_matched": True,
                        "branch_taken": branch_taken,
                        "action": "repeat_body",
                        "status": "looping",
                    }
                else:
                    branch_taken = exit_target or "exit"
                    output = {
                        "iteration": current_iter,
                        "max_iterations": max_iter,
                        "condition_matched": False,
                        "branch_taken": branch_taken,
                        "action": "exit_loop",
                        "status": "completed",
                    }

            elif node.node_type == NodeType.DELEGATE:
                ctrl = node.control_config or node.metadata.get("control_config", {})
                routing_mode = (ctrl.get("routing_mode") or "MANUAL").upper()
                target_agent = node.agent_id or ctrl.get("target_agent_id")

                if routing_mode == "AUTOMATIC":
                    candidates = ctrl.get("candidate_agents") or []
                    if candidates:
                        target_agent = candidates[0]

                if not target_agent:
                    raise ValueError(f"Nodo DELEGATE '{node_id}' no tiene agente asignado ni candidatos válidos.")

                if self.agent_bus:
                    msg = AgentMessage(
                        message_id=f"wf_delegate_{node_id}_{uuid.uuid4().hex[:8]}",
                        sender_id="praxeon_supervisor",
                        receiver_id=target_agent,
                        session_id=self.context.execution_id,
                        task_id=node_id,
                        message_type=MessageType.DELEGATE,
                        priority=MessagePriority.HIGH,
                        payload=resolved_inputs,
                    )
                    self.agent_bus.send(msg)
                    with self._lock:
                        self.context.node_states[node_id] = NodeStatus.WAITING_RESULT
                        self.context.node_outputs[node_id] = {
                            "delegated_to": target_agent,
                            "routing_mode": routing_mode,
                            "message_id": msg.message_id,
                            "status": "waiting_result",
                        }
                    return True
                elif target_agent in self._handlers:
                    output = self._handlers[target_agent](resolved_inputs)
                    output["delegated_to"] = target_agent
                elif self.allow_synthetic_fallback:
                    output = {
                        "delegated_to": target_agent,
                        "routing_mode": routing_mode,
                        "status": "completed",
                        "response": f"Agent {target_agent} completed delegated task",
                    }
                else:
                    raise NotImplementedError(
                        f"No handler or bus configured for delegated agent '{target_agent}' in node '{node_id}'."
                    )

            elif node.node_type == NodeType.HUMAN_APPROVAL:
                ctrl = node.control_config or node.metadata.get("control_config", {})
                prompt = ctrl.get("prompt") or node.inputs.get("prompt") or "Aprobación requerida"
                approver_role = ctrl.get("approver_role") or "supervisor"

                with self._lock:
                    self.context.node_states[node_id] = NodeStatus.WAITING_APPROVAL
                    self.context.node_outputs[node_id] = {
                        "prompt": prompt,
                        "approver_role": approver_role,
                        "status": "waiting_approval",
                        "requested_at": now.isoformat(),
                    }
                return True

            elif node.node_type == NodeType.PARALLEL_JOIN:
                ctrl = node.control_config or node.metadata.get("control_config", {})
                merge_policy = ctrl.get("merge_policy", "shallow")
                cancel_remaining = ctrl.get("cancel_remaining", False)

                incoming_edges = self.workflow.get_incoming_edges(node_id)
                if cancel_remaining:
                    with self._lock:
                        for edge in incoming_edges:
                            st = self.context.node_states.get(edge.from_node)
                            if st in (NodeStatus.RUNNING, NodeStatus.PENDING, NodeStatus.READY):
                                self.context.node_states[edge.from_node] = NodeStatus.CANCELLED

                merged: Dict[str, Any] = {"parallel_sync": True, "status": "completed"}
                if merge_policy == "namespace":
                    for edge in incoming_edges:
                        merged[edge.from_node] = self.context.node_outputs.get(edge.from_node, {})
                else:
                    for edge in incoming_edges:
                        merged.update(self.context.node_outputs.get(edge.from_node, {}))
                output = merged

            elif node.node_type == NodeType.PARALLEL_FORK:
                output = {"parallel_sync": True, "status": "completed"}

        except Exception as exc:
            success = False
            output = {"error": str(exc)}

        # 3. Actualizar estado y salidas del nodo
        with self._lock:
            if success:
                self.context.node_states[node_id] = NodeStatus.COMPLETED
                self.context.node_outputs[node_id] = output
                self.context.execution_history.append(node_id)
                self.context.node_retry_after.pop(node_id, None)

                # Si es END, verificar si el workflow concluyó
                if node.node_type == NodeType.END:
                    self.context.status = WorkflowStatus.COMPLETED
                    self.context.finished_at = now

                # Si una arista saliente apunta a un bucle WHILE, reactivar el nodo WHILE para la siguiente iteración
                for out_edge in self.workflow.get_outgoing_edges(node_id):
                    target_node = self.workflow.nodes[out_edge.to_node]
                    if target_node.node_type == NodeType.WHILE:
                        self.context.node_states[out_edge.to_node] = NodeStatus.READY

                # Evaluar propagación y marcar ramas excluidas como SKIPPED
                self._propagate_skips(node_id)
                return True
            else:
                # Manejo de política de reintentos
                retries = self.context.node_retries.get(node_id, 0)
                if retries < node.retry_policy.max_retries:
                    self.context.node_retries[node_id] = retries + 1
                    delay = node.retry_policy.delay_seconds * (node.retry_policy.backoff_multiplier ** retries)
                    if delay > 0:
                        self.context.node_states[node_id] = NodeStatus.RETRYING
                        self.context.node_retry_after[node_id] = now + timedelta(seconds=delay)
                    else:
                        self.context.node_states[node_id] = NodeStatus.READY
                    return False
                else:
                    self.context.node_states[node_id] = NodeStatus.FAILED
                    self.context.status = WorkflowStatus.FAILED
                    self.context.error_message = f"Fallo en nodo '{node_id}': {output.get('error')}"
                    self.context.finished_at = now
                    return False

    def _reset_loop_body_nodes(self, while_node_id: str, exit_target: Optional[str] = None) -> None:
        """Reinicia los estados de los nodos que forman parte del cuerpo de un bucle WHILE."""
        visited: Set[str] = set()
        queue = deque([while_node_id])
        body_nodes: Set[str] = set()

        while queue:
            curr = queue.popleft()
            for edge in self.workflow.get_outgoing_edges(curr):
                nxt = edge.to_node
                if nxt == exit_target:
                    continue
                if nxt == while_node_id:
                    continue
                if nxt not in visited:
                    visited.add(nxt)
                    body_nodes.add(nxt)
                    queue.append(nxt)

        with self._lock:
            for nid in body_nodes:
                self.context.node_states[nid] = NodeStatus.PENDING
                self.context.node_retries[nid] = 0

    def approve_node(self, node_id: str, approved: bool = True, comment: str = "") -> bool:
        """Aprueba o rechaza un nodo en estado WAITING_APPROVAL."""
        with self._lock:
            st = self.context.node_states.get(node_id)
            if st != NodeStatus.WAITING_APPROVAL:
                return False

            node = self.workflow.nodes.get(node_id)
            ctrl = node.control_config or node.metadata.get("control_config", {}) if node else {}
            now = datetime.now(timezone.utc)

            if approved:
                self.context.node_states[node_id] = NodeStatus.COMPLETED
                self.context.node_outputs[node_id] = {
                    "status": "approved",
                    "approved": True,
                    "branch_taken": "approved",
                    "comment": comment,
                    "approved_at": now.isoformat(),
                }
                self.context.execution_history.append(node_id)
                self._propagate_skips(node_id)
                return True
            else:
                reject_target = ctrl.get("reject_target")
                if reject_target:
                    self.context.node_states[node_id] = NodeStatus.COMPLETED
                    self.context.node_outputs[node_id] = {
                        "status": "rejected",
                        "approved": False,
                        "branch_taken": "rejected",
                        "comment": comment,
                        "rejected_at": now.isoformat(),
                    }
                    self.context.execution_history.append(node_id)
                    self._propagate_skips(node_id)
                    return True
                else:
                    self.context.node_states[node_id] = NodeStatus.FAILED
                    self.context.status = WorkflowStatus.FAILED
                    self.context.error_message = f"Nodo '{node_id}' rechazado por supervisión: {comment}"
                    self.context.finished_at = now
                    return False

    def _propagate_skips(self, completed_node_id: str) -> None:
        """Marca como SKIPPED los nodos downstream cuyas condiciones nunca se satisficieron o cuyas ramas no fueron tomadas."""
        outgoing = self.workflow.get_outgoing_edges(completed_node_id)
        if not outgoing:
            return

        node = self.workflow.nodes[completed_node_id]
        node_output = self.context.node_outputs.get(completed_node_id, {})
        eval_ctx = self.get_evaluation_context(upstream_node_id=completed_node_id)
        branch_taken = node_output.get("branch_taken")

        for edge in outgoing:
            is_taken = True
            edge_label = (edge.label or "").strip().lower()

            if branch_taken is not None:
                b_str = str(branch_taken).strip().lower()
                if node.node_type in (NodeType.DECISION, NodeType.IF):
                    if edge.edge_id == branch_taken or edge_label == b_str:
                        is_taken = True
                    elif b_str == "true" and edge_label in ("true", "yes", "si", "1"):
                        is_taken = True
                    elif b_str == "false" and edge_label in ("false", "no", "0"):
                        is_taken = True
                    elif b_str == "default" and edge_label in ("default", "else"):
                        is_taken = True
                    elif edge_label in ("true", "false", "yes", "no", "default", "else"):
                        is_taken = False
                elif node.node_type == NodeType.WHILE:
                    if b_str in ("repeat_body", "body") and edge_label in ("exit", "false"):
                        is_taken = False
                    elif b_str in ("exit_loop", "exit") and edge_label in ("body", "true"):
                        is_taken = False

            if is_taken and edge.condition:
                legacy_ctx = {"output": node_output, **self.context.variables, **node_output}
                is_taken = edge.condition.evaluate(eval_ctx) or edge.condition.evaluate(legacy_ctx)

            if not is_taken:
                target_id = edge.to_node
                if not self.is_node_ready(target_id) and self.context.node_states[target_id] in (NodeStatus.PENDING, NodeStatus.READY):
                    other_satisfied = any(
                        other_edge.from_node != completed_node_id and
                        self.context.node_states.get(other_edge.from_node) == NodeStatus.COMPLETED
                        for other_edge in self.workflow.get_incoming_edges(target_id)
                    )
                    if not other_satisfied:
                        self.context.node_states[target_id] = NodeStatus.SKIPPED
                        self._propagate_skips(target_id)

    def _check_agent_responses(self) -> List[str]:
        """Revisa si hay mensajes de respuesta en el AgentMessageBus para nodos en estado WAITING_RESULT."""
        if not self.agent_bus:
            return []

        completed_nodes = []
        while True:
            msg = self.agent_bus.receive("praxeon_supervisor")
            if not msg:
                break

            task_node_id = msg.task_id or msg.correlation_id
            if task_node_id and task_node_id in self.workflow.nodes:
                st = self.context.node_states.get(task_node_id)
                if st in (NodeStatus.WAITING_RESULT, NodeStatus.RUNNING):
                    self.context.node_outputs[task_node_id] = msg.payload
                    self.context.node_states[task_node_id] = NodeStatus.COMPLETED
                    self.context.execution_history.append(task_node_id)
                    self._propagate_skips(task_node_id)
                    completed_nodes.append(task_node_id)
        return completed_nodes

    def handle_agent_response(self, msg: AgentMessage) -> bool:
        """Permite inyectar o procesar directamente la respuesta de un agente para un nodo en espera."""
        task_node_id = msg.task_id or msg.correlation_id
        if not task_node_id or task_node_id not in self.workflow.nodes:
            return False

        st = self.context.node_states.get(task_node_id)
        if st in (NodeStatus.WAITING_RESULT, NodeStatus.RUNNING):
            self.context.node_outputs[task_node_id] = msg.payload
            self.context.node_states[task_node_id] = NodeStatus.COMPLETED
            self.context.execution_history.append(task_node_id)
            self._propagate_skips(task_node_id)
            return True
        return False

    def check_scheduled_retries_and_timeouts(
        self,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, List[str]]:
        """Evalúa deadlines de timeout y programas de reintento para nodos activos o pendientes.

        Retorna un diccionario con:
        - 'ready_from_retry': lista de node_ids reactivados a NodeStatus.READY.
        - 'timed_out': lista de node_ids marcados con TIMEOUT o programados para reintento tras timeout.
        """
        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        ready_from_retry: List[str] = []
        timed_out: List[str] = []

        # 1. Evaluar reintentos programados (RETRYING -> READY cuando now >= retry_after)
        for node_id, status in list(self.context.node_states.items()):
            if status == NodeStatus.RETRYING:
                retry_after = self.context.node_retry_after.get(node_id)
                if retry_after is None or now >= retry_after:
                    self.context.node_states[node_id] = NodeStatus.READY
                    self.context.node_retry_after.pop(node_id, None)
                    ready_from_retry.append(node_id)

        # 2. Evaluar timeouts en nodos activos (RUNNING, WAITING_RESULT)
        for node_id, status in list(self.context.node_states.items()):
            if status in (NodeStatus.RUNNING, NodeStatus.WAITING_RESULT):
                node = self.workflow.nodes.get(node_id)
                if not node or node.timeout_seconds is None:
                    continue
                started_at = self.context.node_started_at.get(node_id)
                if not started_at:
                    continue
                if (now - started_at).total_seconds() >= node.timeout_seconds:
                    timed_out.append(node_id)
                    retries = self.context.node_retries.get(node_id, 0)
                    if retries < node.retry_policy.max_retries:
                        self.context.node_retries[node_id] = retries + 1
                        delay = node.retry_policy.delay_seconds * (node.retry_policy.backoff_multiplier ** retries)
                        if delay > 0:
                            self.context.node_states[node_id] = NodeStatus.RETRYING
                            self.context.node_retry_after[node_id] = now + timedelta(seconds=delay)
                        else:
                            self.context.node_states[node_id] = NodeStatus.READY
                    else:
                        self.context.node_states[node_id] = NodeStatus.TIMEOUT
                        self.context.status = WorkflowStatus.FAILED
                        self.context.error_message = (
                            f"Timeout de {node.timeout_seconds}s excedido en nodo '{node_id}' tras {retries} reintentos."
                        )
                        self.context.finished_at = now

        return {
            "ready_from_retry": ready_from_retry,
            "timed_out": timed_out,
        }

    def step(self, current_time: Optional[datetime] = None) -> Optional[str]:
        """Avanza la ejecución un paso ejecutando el siguiente nodo elegible."""
        if self.context.status not in (WorkflowStatus.RUNNING, WorkflowStatus.IDLE):
            return None

        if self.context.status == WorkflowStatus.IDLE:
            self.start()

        # 0. Evaluar timeouts y reintentos programados
        self.check_scheduled_retries_and_timeouts(current_time=current_time)
        if self.context.status not in (WorkflowStatus.RUNNING, WorkflowStatus.IDLE):
            return None

        # 1. Comprobar si hay respuestas de agentes que resuelvan nodos en WAITING_RESULT
        resolved_from_bus = self._check_agent_responses()
        if resolved_from_bus:
            end_nodes = self.workflow.get_end_nodes()
            if any(self.context.node_states.get(e.node_id) == NodeStatus.COMPLETED for e in end_nodes):
                self.context.status = WorkflowStatus.COMPLETED
                self.context.finished_at = datetime.now(timezone.utc)
            return resolved_from_bus[0]

        # 2. Localizar siguiente nodo listo para ejecutarse
        next_nid = self.find_next_ready_node()
        if not next_nid:
            # Si hay nodos en espera asíncrona o reintento, el workflow sigue esperando
            waiting_nodes = [
                nid for nid, st in self.context.node_states.items()
                if st in (NodeStatus.WAITING_RESULT, NodeStatus.WAITING_APPROVAL, NodeStatus.RETRYING)
            ]
            if waiting_nodes:
                return None

            # Si no hay nodos listos ni esperando, verificar si concluyó
            end_nodes = self.workflow.get_end_nodes()
            if any(self.context.node_states.get(e.node_id) == NodeStatus.COMPLETED for e in end_nodes):
                self.context.status = WorkflowStatus.COMPLETED
                self.context.finished_at = datetime.now(timezone.utc)
            return None

        self.execute_node(next_nid, current_time=current_time)
        return next_nid

    def step_concurrent(
        self,
        max_workers: int = 8,
        current_time: Optional[datetime] = None,
    ) -> List[str]:
        """Avanza la ejecución despachando concurrentemente todos los nodos listos.

        Si existen ramas paralelas activas (ej. tras un PARALLEL_FORK), despacha sus
        tareas a un pool de hilos para ejecución física simultánea en lugar de serial.
        """
        with self._lock:
            if self.context.status not in (WorkflowStatus.RUNNING, WorkflowStatus.IDLE):
                return []

            if self.context.status == WorkflowStatus.IDLE:
                self.start()

            # 0. Evaluar timeouts y reintentos programados
            self.check_scheduled_retries_and_timeouts(current_time=current_time)
            if self.context.status not in (WorkflowStatus.RUNNING, WorkflowStatus.IDLE):
                return []

            # 1. Comprobar si hay respuestas de agentes que resuelvan nodos en WAITING_RESULT
            resolved_from_bus = self._check_agent_responses()
            if resolved_from_bus:
                end_nodes = self.workflow.get_end_nodes()
                if any(self.context.node_states.get(e.node_id) == NodeStatus.COMPLETED for e in end_nodes):
                    self.context.status = WorkflowStatus.COMPLETED
                    self.context.finished_at = datetime.now(timezone.utc)
                return resolved_from_bus

            # 2. Localizar todos los nodos listos
            ready_nodes = self.find_all_ready_nodes()
            if not ready_nodes:
                waiting_nodes = [
                    nid for nid, st in self.context.node_states.items()
                    if st in (NodeStatus.WAITING_RESULT, NodeStatus.WAITING_APPROVAL, NodeStatus.RETRYING)
                ]
                if waiting_nodes:
                    return []

                end_nodes = self.workflow.get_end_nodes()
                if any(self.context.node_states.get(e.node_id) == NodeStatus.COMPLETED for e in end_nodes):
                    self.context.status = WorkflowStatus.COMPLETED
                    self.context.finished_at = datetime.now(timezone.utc)
                return []

        # Si solo hay 1 nodo listo, ejecución directa
        if len(ready_nodes) == 1:
            self.execute_node(ready_nodes[0], current_time=current_time)
            return ready_nodes

        # Si hay múltiples nodos listos en paralelo, despachar concurrentemente
        workers = min(max_workers, len(ready_nodes))
        executed_nodes: List[str] = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_node = {
                executor.submit(self.execute_node, nid, current_time): nid
                for nid in ready_nodes
            }
            for future in as_completed(future_to_node):
                nid = future_to_node[future]
                try:
                    future.result()
                    executed_nodes.append(nid)
                except Exception as exc:
                    with self._lock:
                        self.context.node_states[nid] = NodeStatus.FAILED
                        self.context.status = WorkflowStatus.FAILED
                        self.context.error_message = f"Fallo en ejecución concurrente de nodo '{nid}': {exc}"
                    executed_nodes.append(nid)

        with self._lock:
            end_nodes = self.workflow.get_end_nodes()
            if any(self.context.node_states.get(e.node_id) == NodeStatus.COMPLETED for e in end_nodes):
                self.context.status = WorkflowStatus.COMPLETED
                if not self.context.finished_at:
                    self.context.finished_at = datetime.now(timezone.utc)

        return executed_nodes

    def run_to_completion(
        self,
        max_steps: int = 100,
        current_time: Optional[datetime] = None,
        concurrent: bool = True,
        max_workers: int = 8,
    ) -> WorkflowExecutionContext:
        """Ejecuta iterativamente el flujo hasta su conclusión o bloqueo."""
        if self.context.status == WorkflowStatus.IDLE:
            self.start()

        steps = 0
        while self.context.status == WorkflowStatus.RUNNING and steps < max_steps:
            if concurrent:
                executed = self.step_concurrent(max_workers=max_workers, current_time=current_time)
            else:
                res = self.step(current_time=current_time)
                executed = [res] if res else []
            steps += 1
            if not executed:
                break

        return self.context

    def retry_node(self, node_id: str) -> bool:
        """Reinicia manualmente un nodo fallido, cancelado o en timeout para reintentar su ejecución."""
        if node_id not in self.workflow.nodes:
            return False

        current_st = self.context.node_states.get(node_id)
        if current_st in (NodeStatus.FAILED, NodeStatus.CANCELLED, NodeStatus.TIMEOUT, NodeStatus.RETRYING):
            self.context.node_states[node_id] = NodeStatus.READY
            self.context.node_retry_after.pop(node_id, None)
            if self.context.status in (WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                self.context.status = WorkflowStatus.RUNNING
                self.context.error_message = None
            return True
        return False

    def backtrack_to_node(self, target_node_id: str) -> bool:
        """Ejecuta un retroceso determinista (backtrack) al estado inmediatamente anterior al nodo especificado.
        
        Restaura variables, borra salidas de nodos descendientes y los restablece a PENDING.
        """
        if target_node_id not in self.workflow.nodes:
            return False

        # Buscar el último checkpoint donde target_node_id estaba a punto de ejecutarse
        target_ckpt: Optional[ExecutionCheckpoint] = None
        for ckpt in reversed(self.context.checkpoints):
            if ckpt.target_node_id == target_node_id:
                target_ckpt = ckpt
                break

        if not target_ckpt:
            # Si no hay checkpoint específico, restaurar al primer checkpoint
            if not self.context.checkpoints:
                return False
            target_ckpt = self.context.checkpoints[0]

        # Restaurar estado
        self.context.restore_checkpoint(target_ckpt)
        self.context.node_states[target_node_id] = NodeStatus.READY
        self.context.status = WorkflowStatus.RUNNING

        # Limpiar descendientes en el grafo
        visited: Set[str] = set()
        queue = deque([target_node_id])
        while queue:
            curr = queue.popleft()
            for child_id in self.workflow.get_downstream_node_ids(curr):
                if child_id not in visited:
                    visited.add(child_id)
                    self.context.node_states[child_id] = NodeStatus.PENDING
                    self.context.node_outputs.pop(child_id, None)
                    queue.append(child_id)

        return True
