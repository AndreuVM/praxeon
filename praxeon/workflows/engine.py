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
from datetime import datetime, timedelta, UTC
import functools
import threading
from typing import Any
from collections.abc import Callable
import uuid
from pydantic import BaseModel, ConfigDict, Field

from praxeon.agents.bus import AgentMessageBus
from praxeon.agents.protocol import AgentMessage, MessageType
from praxeon.workflows.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowNode,
    WorkflowStatus,
)
from praxeon.workflows.node_execution import execute_node
from praxeon.workflows.supervised import SupervisedToolRunner
from praxeon.workflows.semantics import (
    EvaluationContext,
)



def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Fusión profunda para merge_policy='reduce': dicts recursivos, listas concatenadas, escalares: gana el último."""
    result = dict(base)
    for key, value in extra.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _deep_merge(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            result[key] = current + value
        else:
            result[key] = value
    return result


def _synchronized(method):
    """Ejecuta el método bajo el RLock del motor (las mutaciones de estado nunca son concurrentes)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class ExecutionCheckpoint(BaseModel):
    """Punto de restauración inmutable del estado del grafo para retroceso determinista."""
    model_config = ConfigDict(frozen=True)

    checkpoint_id: str
    target_node_id: str | None = None
    node_states: dict[str, NodeStatus]
    node_outputs: dict[str, dict[str, Any]]
    variables: dict[str, Any]
    execution_history: list[str]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkflowExecutionContext:
    """Contexto y estado mutable de una ejecución activa de workflow."""

    MAX_CHECKPOINTS = 200

    def __init__(self, workflow: WorkflowDefinition, execution_id: str | None = None):
        self.execution_id = execution_id or f"exec_{uuid.uuid4().hex[:12]}"
        self.workflow_id = workflow.workflow_id
        self.status = WorkflowStatus.IDLE
        self.node_states: dict[str, NodeStatus] = {nid: NodeStatus.PENDING for nid in workflow.nodes}
        self.node_outputs: dict[str, dict[str, Any]] = {}
        self.node_retries: dict[str, int] = {nid: 0 for nid in workflow.nodes}
        self.node_started_at: dict[str, datetime] = {}
        self.node_retry_after: dict[str, datetime] = {}
        self.variables: dict[str, Any] = dict(workflow.variables)
        self.execution_history: list[str] = []
        self.checkpoints: list[ExecutionCheckpoint] = []
        self.error_message: str | None = None
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None

    def create_checkpoint(self, current_node_id: str | None = None) -> ExecutionCheckpoint:
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
        # Acota la memoria/persistencia: se conservan el checkpoint inicial y los más recientes
        if len(self.checkpoints) > self.MAX_CHECKPOINTS:
            del self.checkpoints[1:len(self.checkpoints) - self.MAX_CHECKPOINTS + 1]
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

    def to_dict(self) -> dict[str, Any]:
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
        agent_bus: AgentMessageBus | None = None,
        task_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
        tool_registry: Any | None = None,
        allow_synthetic_fallback: bool = False,
        supervised_runner: SupervisedToolRunner | None = None,
        node_runner: Callable[[WorkflowNode, dict[str, Any], "WorkflowEngine"], dict[str, Any]] | None = None,
        execution: WorkflowExecution | None = None,
        execution_id: str | None = None,
    ):
        self.workflow = workflow
        self.agent_bus = agent_bus
        self.tool_registry = tool_registry
        self.allow_synthetic_fallback = allow_synthetic_fallback
        self.supervised_runner = supervised_runner
        # Ejecutor de nodos de agente/tarea abstracta (mini-misión supervisada). Si existe, sustituye al bus de agentes
        self.node_runner = node_runner
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = dict(task_handlers or {})
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
        execution_id: str | None = None,
        initial_variables: dict[str, Any] | None = None,
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
        handler: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        """Registra un ejecutor local para tareas o herramientas específicas."""
        self._handlers[tool_or_action] = handler

    def start(self, initial_variables: dict[str, Any] | None = None) -> WorkflowExecutionContext:
        """Inicia el ciclo de ejecución del flujo de trabajo."""
        if initial_variables:
            self.context.variables.update(initial_variables)

        start_node = self.workflow.get_start_node()
        if not start_node:
            raise ValueError("El workflow no contiene un nodo START válido.")

        self.context.status = WorkflowStatus.RUNNING
        self.context.started_at = datetime.now(UTC)
        self.context.node_states[start_node.node_id] = NodeStatus.READY

        # Capturar checkpoint inicial
        self.context.create_checkpoint(current_node_id=start_node.node_id)
        return self.context

    @_synchronized
    def pause(self) -> None:
        """Pausa temporalmente el workflow."""
        if self.context.status == WorkflowStatus.RUNNING:
            self.context.status = WorkflowStatus.PAUSED

    @_synchronized
    def resume(self) -> WorkflowExecutionContext:
        """Reanuda la ejecución de un workflow pausado."""
        if self.context.status == WorkflowStatus.PAUSED:
            self.context.status = WorkflowStatus.RUNNING
        return self.context

    def cancel(self, reason: str = "User cancelled execution") -> None:
        """Cancela la ejecución de todo el workflow (sin efecto si ya terminó)."""
        with self._lock:
            if self.context.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                return
            self._cancel_locked(reason)

    def _cancel_locked(self, reason: str) -> None:
        self.context.status = WorkflowStatus.CANCELLED
        self.context.error_message = reason
        self.context.finished_at = datetime.now(UTC)
        for nid, st in self.context.node_states.items():
            if st in (NodeStatus.PENDING, NodeStatus.READY, NodeStatus.RUNNING):
                self.context.node_states[nid] = NodeStatus.CANCELLED

    @_synchronized
    def get_evaluation_context(self, upstream_node_id: str | None = None) -> EvaluationContext:
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

        # Invariante INV-05: El nodo terminal END no se considera listo mientras existan ramas activas requeridas o bucles en progreso
        if node.node_type == NodeType.END:
            active_nodes = [
                nid for nid, st in self.context.node_states.items()
                if st in (NodeStatus.RUNNING, NodeStatus.WAITING_RESULT, NodeStatus.WAITING_APPROVAL)
            ]
            if active_nodes:
                return False

            # No permitir que END avance si algún bucle WHILE está reactivado en READY
            has_ready_while = any(
                n.node_type == NodeType.WHILE and self.context.node_states.get(nid) == NodeStatus.READY
                for nid, n in self.workflow.nodes.items()
            )
            if has_ready_while:
                return False

            # No permitir que END avance si hay un bucle activo en iteración (status == 'looping')
            for nid, n in self.workflow.nodes.items():
                if n.node_type == NodeType.WHILE:
                    out = self.context.node_outputs.get(nid, {})
                    decision = out.get("decision") or out.get("action")
                    if decision in ("CONTINUE", "repeat_body") and out.get("status") == "looping":
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

                # Si el predecesor fue un nodo de bucle estructurado WHILE
                elif upstream_node.node_type == NodeType.WHILE:
                    decision = str(upstream_output.get("decision") or upstream_output.get("action") or "").strip().lower()
                    branch_taken = str(upstream_output.get("branch_taken") or "").strip().lower()
                    ctrl = upstream_node.control_config or upstream_node.metadata.get("control_config", {})
                    body_entry = ctrl.get("body_entry")
                    exit_target = ctrl.get("exit_target")
                    edge_label = (edge.label or "").strip().lower()

                    is_body_edge = (
                        (body_entry and edge.to_node == body_entry)
                        or (body_entry and edge.edge_id == body_entry)
                        or edge_label in ("body", "repeat_body", "true")
                    )
                    is_exit_edge = (
                        (exit_target and edge.to_node == exit_target)
                        or (exit_target and edge.edge_id == exit_target)
                        or edge_label in ("exit", "exit_loop", "false")
                    )

                    # Si el bucle decidió repetir el cuerpo (CONTINUE / repeat_body)
                    if decision in ("continue", "repeat_body") or branch_taken in ("repeat_body", "body", "continue") or (body_entry and branch_taken == str(body_entry).lower()):
                        if is_body_edge:
                            return True
                        if is_exit_edge:
                            continue  # Bloquear la rama de salida
                    # Si el bucle decidió finalizar (EXIT / exit_loop)
                    elif decision in ("exit", "exit_loop") or branch_taken in ("exit_loop", "exit") or (exit_target and branch_taken == str(exit_target).lower()):
                        if is_exit_edge:
                            return True
                        if is_body_edge:
                            continue  # Bloquear la rama del cuerpo
                    else:
                        if is_body_edge or is_exit_edge:
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

    def find_all_ready_nodes(self) -> list[str]:
        """Localiza todos los nodos actualmente listos para ejecutarse de manera independiente."""
        with self._lock:
            return [nid for nid in self.workflow.nodes if self.is_node_ready(nid)]

    def find_next_ready_node(self) -> str | None:
        """Localiza el siguiente nodo listo para ejecutarse según el orden topológico."""
        with self._lock:
            ready = self.find_all_ready_nodes()
            return ready[0] if ready else None

    def get_resolved_inputs_for(self, node_id: str) -> dict[str, Any]:
        """Entradas del nodo resueltas desde variables, salidas previas y `inputs` propios."""
        resolved = dict(self.context.variables)
        for up_id in self.workflow.get_upstream_node_ids(node_id):
            if up_id in self.context.node_outputs:
                resolved.update(self.context.node_outputs[up_id])
        resolved.update(self.workflow.nodes[node_id].inputs)
        return resolved

    def execute_node(self, node_id: str, current_time: datetime | None = None) -> bool:
        """Ejecuta un nodo individual, despacha tareas/agentes y propaga outputs.

        Los pasos (preparar, ejecutar según el tipo de nodo, registrar el resultado) están en
        ``praxeon.workflows.node_execution``. Devuelve True si el nodo avanzó (completado o a la espera) y False si
        falló, queda para reintento o la ejecución se canceló mientras corría.
        """
        return execute_node(self, node_id, current_time)

    def _reset_loop_body_nodes(self, while_node_id: str, exit_target: str | None = None) -> None:
        """Reinicia los estados de los nodos que forman parte del cuerpo de un bucle WHILE."""
        visited: set[str] = set()
        queue = deque([while_node_id])
        body_nodes: set[str] = set()

        while queue:
            curr = queue.popleft()
            for edge in self.workflow.get_outgoing_edges(curr):
                nxt = edge.to_node
                if nxt == while_node_id:
                    continue
                edge_label = (edge.label or "").strip().lower()
                if curr == while_node_id:
                    if exit_target and (nxt == exit_target or edge.edge_id == exit_target):
                        continue
                    if edge_label in ("exit", "exit_loop", "false"):
                        continue
                if exit_target and nxt == exit_target:
                    continue
                if nxt not in visited:
                    visited.add(nxt)
                    body_nodes.add(nxt)
                    queue.append(nxt)

        with self._lock:
            for nid in body_nodes:
                self.context.node_states[nid] = NodeStatus.PENDING
                self.context.node_retries[nid] = 0

    def approve_node(self, node_id: str, approved: bool = True, comment: str = "", approver_id: str = "operator") -> bool:
        """Aprueba o rechaza un nodo en estado WAITING_APPROVAL."""
        with self._lock:
            st = self.context.node_states.get(node_id)
            if st != NodeStatus.WAITING_APPROVAL:
                return False

            node = self.workflow.nodes.get(node_id)
            ctrl = node.control_config or node.metadata.get("control_config", {}) if node else {}
            now = datetime.now(UTC)

            pending = self.context.node_outputs.get(node_id, {})
            if approved and pending.get("awaiting_tool_confirmation") and self.supervised_runner and node:
                # Confirmación de herramienta: se emite un ticket auditable y el nodo se reejecuta (ya autorizado)
                self.supervised_runner.confirm(
                    execution_id=self.context.execution_id,
                    node_id=node_id,
                    tool_name=node.tool_name,
                    arguments={**node.inputs, **{k: v for k, v in self.get_resolved_inputs_for(node_id).items() if k in ("command", "cmd", "path", "content")}},
                    approver_id=approver_id,
                    reason=comment or None,
                )
                self.context.node_states[node_id] = NodeStatus.READY
                return True

            if approved:
                self.context.node_states[node_id] = NodeStatus.COMPLETED
                self.context.node_outputs[node_id] = {
                    "status": "approved",
                    "approved_by": approver_id,
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
                    decision = str(node_output.get("decision") or node_output.get("action") or "").strip().lower()
                    ctrl = node.control_config or node.metadata.get("control_config", {})
                    body_entry = str(ctrl.get("body_entry") or "").strip().lower()
                    exit_target = str(ctrl.get("exit_target") or "").strip().lower()

                    is_body_edge = (
                        (body_entry and edge.to_node.lower() == body_entry)
                        or (body_entry and edge.edge_id.lower() == body_entry)
                        or edge_label in ("body", "repeat_body", "true")
                    )
                    is_exit_edge = (
                        (exit_target and edge.to_node.lower() == exit_target)
                        or (exit_target and edge.edge_id.lower() == exit_target)
                        or edge_label in ("exit", "exit_loop", "false")
                    )

                    if decision in ("continue", "repeat_body") or b_str in ("repeat_body", "body", "continue") or (body_entry and b_str == body_entry):
                        if is_exit_edge:
                            is_taken = False
                        elif is_body_edge:
                            is_taken = True
                    elif decision in ("exit", "exit_loop") or b_str in ("exit_loop", "exit") or (exit_target and b_str == exit_target):
                        if is_body_edge:
                            is_taken = False
                        elif is_exit_edge:
                            is_taken = True

            if is_taken and edge.condition:
                legacy_ctx = {"output": node_output, **self.context.variables, **node_output}
                is_taken = edge.condition.evaluate(eval_ctx) or edge.condition.evaluate(legacy_ctx)

            if not is_taken:
                # Si el nodo es WHILE y está continuando en el cuerpo, NO saltar la salida
                if node.node_type == NodeType.WHILE:
                    decision = str(node_output.get("decision") or node_output.get("action") or "").strip().lower()
                    if decision in ("continue", "repeat_body"):
                        continue

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

    def _should_complete_workflow(self) -> bool:
        """Determina si el workflow ha completado todos sus nodos terminales sin bucles ni tareas pendientes."""
        end_nodes = self.workflow.get_end_nodes()
        if not any(self.context.node_states.get(e.node_id) == NodeStatus.COMPLETED for e in end_nodes):
            return False

        # Si aún hay algún nodo en ejecución o espera asíncrona
        active = any(
            st in (NodeStatus.RUNNING, NodeStatus.WAITING_RESULT, NodeStatus.WAITING_APPROVAL, NodeStatus.RETRYING)
            for st in self.context.node_states.values()
        )
        if active:
            return False

        # Si algún bucle WHILE está en READY o activo en repetición
        has_active_while = any(
            n.node_type == NodeType.WHILE and (
                self.context.node_states.get(nid) == NodeStatus.READY
                or str(self.context.node_outputs.get(nid, {}).get("decision") or self.context.node_outputs.get(nid, {}).get("action")).strip().lower() in ("continue", "repeat_body")
            )
            for nid, n in self.workflow.nodes.items()
        )
        if has_active_while:
            return False

        return True

    def _finalize_terminal_state(self, status: WorkflowStatus = WorkflowStatus.COMPLETED) -> None:
        """Limpia y garantiza consistencia terminal del workflow.

        Transiciona nodos residuales en PENDING/READY a SKIPPED y asegura que el timestamp
        finished_at y el status final queden establecidos deterministamente.
        """
        with self._lock:
            # Un estado terminal ya establecido (p. ej. CANCELLED concurrente) no se sobrescribe
            if self.context.status in (WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                return
            now = datetime.now(UTC)
            self.context.status = status
            if not self.context.finished_at:
                self.context.finished_at = now

            for nid, st in list(self.context.node_states.items()):
                if st in (NodeStatus.PENDING, NodeStatus.READY):
                    self.context.node_states[nid] = NodeStatus.SKIPPED
                elif st == NodeStatus.RUNNING:
                    if status != WorkflowStatus.COMPLETED:
                        self.context.node_states[nid] = NodeStatus.CANCELLED

    def _expected_agent_for(self, node_id: str) -> str | None:
        """Agente legítimo cuya respuesta puede resolver el nodo (el despachado o el delegado)."""
        out = self.context.node_outputs.get(node_id, {})
        return out.get("delegated_to") or out.get("agent_id") or self.workflow.nodes[node_id].agent_id

    def _apply_agent_response(self, msg: AgentMessage) -> bool:
        """Aplica la respuesta de un agente a su nodo en espera, validando ejecución, emisor y resultado."""
        task_node_id = msg.task_id or msg.correlation_id
        if not task_node_id or task_node_id not in self.workflow.nodes:
            return False
        if msg.session_id and msg.session_id != self.context.execution_id:
            return False  # respuesta de otra ejecución
        with self._lock:
            st = self.context.node_states.get(task_node_id)
            if st not in (NodeStatus.WAITING_RESULT, NodeStatus.RUNNING):
                return False
            expected = self._expected_agent_for(task_node_id)
            if expected and msg.sender_id != expected:
                return False  # emisor no autorizado para este nodo
            dispatched_id = self.context.node_outputs.get(task_node_id, {}).get("message_id")
            if dispatched_id and (msg.in_reply_to or msg.correlation_id) not in (None, dispatched_id)                     and (msg.in_reply_to or msg.correlation_id) != task_node_id:
                return False  # respuesta tardía de un intento anterior
            payload = msg.payload if isinstance(msg.payload, dict) else {"result": msg.payload}
            if msg.message_type in (MessageType.AGENT_NACK, MessageType.ALERT) or payload.get("error"):
                self.context.node_states[task_node_id] = NodeStatus.FAILED
                self.context.node_outputs[task_node_id] = payload
                self.context.status = WorkflowStatus.FAILED
                self.context.error_message = f"Agente '{msg.sender_id}' reportó fallo en nodo '{task_node_id}': {payload.get('error') or msg.message_type.value}"
                self.context.finished_at = datetime.now(UTC)
                return True
            self.context.node_outputs[task_node_id] = payload
            self.context.node_states[task_node_id] = NodeStatus.COMPLETED
            self.context.execution_history.append(task_node_id)
            self._propagate_skips(task_node_id)
            return True

    def _check_agent_responses(self) -> list[str]:
        """Consume del buzón solo las respuestas de esta ejecución para nodos en estado WAITING_RESULT."""
        if not self.agent_bus:
            return []

        completed_nodes: list[str] = []
        exec_id = self.context.execution_id

        def mine(m: AgentMessage) -> bool:
            return (not m.session_id or m.session_id == exec_id) and (m.task_id or m.correlation_id) in self.workflow.nodes

        while True:
            msg = self.agent_bus.receive_matching("praxeon_supervisor", mine)
            if not msg:
                break
            if self._apply_agent_response(msg):
                completed_nodes.append(msg.task_id or msg.correlation_id)
        return completed_nodes

    def handle_agent_response(self, msg: AgentMessage) -> bool:
        """Permite inyectar o procesar directamente la respuesta de un agente para un nodo en espera."""
        return self._apply_agent_response(msg)

    @_synchronized
    def check_global_timeout(self, current_time: datetime | None = None) -> bool:
        """Aplica el timeout global del workflow. Devuelve True si la ejecución expiró."""
        timeout_sec = getattr(self.workflow, "timeout_seconds", None)
        ctx = self.context
        if not timeout_sec or not ctx.started_at or ctx.status not in (WorkflowStatus.RUNNING, WorkflowStatus.PAUSED):
            return False
        now = current_time or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        if (now - ctx.started_at).total_seconds() < timeout_sec:
            return False
        ctx.status = WorkflowStatus.FAILED
        ctx.error_message = f"Timeout global de workflow ({timeout_sec}s) excedido."
        ctx.finished_at = now
        for nid, st in ctx.node_states.items():
            if st in (NodeStatus.RUNNING, NodeStatus.WAITING_RESULT, NodeStatus.READY, NodeStatus.PENDING):
                ctx.node_states[nid] = NodeStatus.TIMEOUT
        return True

    def tick(self, current_time: datetime | None = None) -> dict[str, Any]:
        """Mantenimiento común a todos los orquestadores: timeout global, reintentos, timeouts de nodo y respuestas de agentes."""
        if self.check_global_timeout(current_time=current_time):
            return {"timed_out_workflow": True, "ready_from_retry": [], "timed_out": [], "agent_resolved": []}
        res = self.check_scheduled_retries_and_timeouts(current_time=current_time)
        resolved = self._check_agent_responses() if self.context.status == WorkflowStatus.RUNNING else []
        return {"timed_out_workflow": False, **res, "agent_resolved": resolved}

    @_synchronized
    def check_scheduled_retries_and_timeouts(
        self,
        current_time: datetime | None = None,
    ) -> dict[str, list[str]]:
        """Evalúa deadlines de timeout y programas de reintento para nodos activos o pendientes.

        Retorna un diccionario con:
        - 'ready_from_retry': lista de node_ids reactivados a NodeStatus.READY.
        - 'timed_out': lista de node_ids marcados con TIMEOUT o programados para reintento tras timeout.
        """
        now = current_time or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        ready_from_retry: list[str] = []
        timed_out: list[str] = []

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

    def step(self, current_time: datetime | None = None) -> str | None:
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
            if self._should_complete_workflow():
                self._finalize_terminal_state()
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
            if self._should_complete_workflow():
                self._finalize_terminal_state()
            return None

        self.execute_node(next_nid, current_time=current_time)
        return next_nid

    def step_concurrent(
        self,
        max_workers: int = 8,
        current_time: datetime | None = None,
    ) -> list[str]:
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
                if self._should_complete_workflow():
                    self._finalize_terminal_state()
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

                if self._should_complete_workflow():
                    self._finalize_terminal_state()
                return []

        # Si solo hay 1 nodo listo, ejecución directa
        if len(ready_nodes) == 1:
            self.execute_node(ready_nodes[0], current_time=current_time)
            return ready_nodes

        # Si hay múltiples nodos listos en paralelo, despachar concurrentemente
        workers = min(max_workers, len(ready_nodes))
        executed_nodes: list[str] = []
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
            if self._should_complete_workflow():
                self._finalize_terminal_state()

        return executed_nodes

    def run_to_completion(
        self,
        max_steps: int = 100,
        current_time: datetime | None = None,
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

        if self.context.status == WorkflowStatus.RUNNING and self._should_complete_workflow():
            self._finalize_terminal_state()

        return self.context

    @_synchronized
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
                self.context.finished_at = None
            return True
        return False

    @_synchronized
    def skip_node(self, node_id: str, reason: str = "Omitido por el operador") -> bool:
        """Da por resuelto un nodo fallido sin ejecutarlo (decisión del operador) y reanuda el flujo."""
        if self.context.node_states.get(node_id) not in (NodeStatus.FAILED, NodeStatus.TIMEOUT, NodeStatus.CANCELLED):
            return False
        self.context.node_states[node_id] = NodeStatus.COMPLETED
        self.context.node_outputs[node_id] = {"status": "skipped", "skipped_by_operator": True, "response": f"({reason})"}
        self.context.execution_history.append(node_id)
        self.context.node_retry_after.pop(node_id, None)
        if self.context.status in (WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
            self.context.status = WorkflowStatus.RUNNING
            self.context.error_message = None
            self.context.finished_at = None
        self._propagate_skips(node_id)
        if self._should_complete_workflow():
            self._finalize_terminal_state()
        return True

    @_synchronized
    def backtrack_to_node(self, target_node_id: str) -> bool:
        """Ejecuta un retroceso determinista (backtrack) al estado inmediatamente anterior al nodo especificado.
        
        Restaura variables, borra salidas de nodos descendientes y los restablece a PENDING.
        """
        if target_node_id not in self.workflow.nodes:
            return False

        # Buscar el último checkpoint donde target_node_id estaba a punto de ejecutarse
        target_ckpt: ExecutionCheckpoint | None = None
        for ckpt in reversed(self.context.checkpoints):
            if ckpt.target_node_id == target_node_id:
                target_ckpt = ckpt
                break

        if not target_ckpt:
            # Si no hay checkpoint específico, restaurar al primer checkpoint
            if not self.context.checkpoints:
                return False
            target_ckpt = self.context.checkpoints[0]

        # Restaurar estado (el rollback es solo de estado: las acciones ya ejecutadas no se compensan)
        self.context.restore_checkpoint(target_ckpt)
        self.context.node_states[target_node_id] = NodeStatus.READY
        self.context.status = WorkflowStatus.RUNNING
        self.context.finished_at = None
        # Los checkpoints posteriores al punto de restauración dejan de ser válidos
        idx = self.context.checkpoints.index(target_ckpt)
        del self.context.checkpoints[idx + 1:]
        # Contadores de reintento y temporizadores ligados al tramo descartado
        self.context.node_retries = {nid: 0 for nid in self.workflow.nodes}
        self.context.node_started_at.clear()
        self.context.node_retry_after.clear()

        # Limpiar descendientes en el grafo
        visited: set[str] = set()
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
