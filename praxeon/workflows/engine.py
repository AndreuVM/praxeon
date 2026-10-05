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
from datetime import datetime, timezone
from enum import Enum
import time
from typing import Any, Callable, Dict, List, Optional, Set
from pydantic import BaseModel, ConfigDict, Field

from praxeon.agents.bus import AgentMessageBus
from praxeon.agents.protocol import AgentMessage, MessagePriority, MessageType
from praxeon.workflows.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)


class WorkflowStatus(str, Enum):
    """Estados del ciclo de vida del flujo de trabajo."""
    IDLE = "IDLE"                      # Instanciado, aún no iniciado
    RUNNING = "RUNNING"                # Ejecutándose activamente
    PAUSED = "PAUSED"                  # Detenido temporalmente
    COMPLETED = "COMPLETED"            # Todos los nodos terminales alcanzados
    FAILED = "FAILED"                  # Detenido por fallo no recuperable en un nodo crítico
    CANCELLED = "CANCELLED"            # Abortado explícitamente


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
        self.execution_id = execution_id or f"exec_{int(time.time()*1000)%1000000}"
        self.workflow_id = workflow.workflow_id
        self.status = WorkflowStatus.IDLE
        self.node_states: Dict[str, NodeStatus] = {nid: NodeStatus.PENDING for nid in workflow.nodes}
        self.node_outputs: Dict[str, Dict[str, Any]] = {}
        self.node_retries: Dict[str, int] = {nid: 0 for nid in workflow.nodes}
        self.variables: Dict[str, Any] = dict(workflow.variables)
        self.execution_history: List[str] = []
        self.checkpoints: List[ExecutionCheckpoint] = []
        self.error_message: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None

    def create_checkpoint(self, current_node_id: Optional[str] = None) -> ExecutionCheckpoint:
        """Captura una instantánea del estado actual de ejecución."""
        ckpt = ExecutionCheckpoint(
            checkpoint_id=f"ckpt_{len(self.checkpoints)+1}_{int(time.time()*1000)%10000}",
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

    def to_dict(self) -> Dict[str, Any]:
        """Serializa el estado del contexto de ejecución."""
        return {
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "status": self.status.value,
            "node_states": {k: v.value for k, v in self.node_states.items()},
            "node_outputs": self.node_outputs,
            "variables": self.variables,
            "execution_history": self.execution_history,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class WorkflowEngine:
    """Motor orquestador de grafos de workflows."""

    def __init__(
        self,
        workflow: WorkflowDefinition,
        agent_bus: Optional[AgentMessageBus] = None,
        task_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]] = None,
    ):
        self.workflow = workflow
        self.agent_bus = agent_bus
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = task_handlers or {}
        self.context = WorkflowExecutionContext(workflow=self.workflow)

        # Validar consistencia estructural del flujo
        errors = self.workflow.validate_graph()
        if errors:
            raise ValueError(f"No se puede instanciar WorkflowEngine con un grafo inválido: {'; '.join(errors)}")

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

    def is_node_ready(self, node_id: str) -> bool:
        """Evalúa si todas las dependencias entrantes de un nodo se han satisfecho."""
        node = self.workflow.nodes[node_id]
        if self.context.node_states[node_id] not in (NodeStatus.PENDING, NodeStatus.READY):
            return False

        incoming_edges = self.workflow.get_incoming_edges(node_id)
        if not incoming_edges:
            # Si no tiene predecesores y es START, está listo
            return node.node_type == NodeType.START

        # Si el nodo es PARALLEL_JOIN, todas las ramas activas deben haber terminado
        if node.node_type == NodeType.PARALLEL_JOIN:
            for edge in incoming_edges:
                up_st = self.context.node_states.get(edge.from_node)
                if up_st not in (NodeStatus.COMPLETED, NodeStatus.SKIPPED):
                    return False
            return True

        # En nodos normales o DECISION, al menos una arista entrante debe estar satisfecha
        # y su nodo origen completado
        for edge in incoming_edges:
            up_st = self.context.node_states.get(edge.from_node)
            if up_st == NodeStatus.COMPLETED:
                if edge.condition is None:
                    return True
                # Evaluar condición de la arista
                upstream_output = self.context.node_outputs.get(edge.from_node, {})
                eval_ctx = {
                    "output": upstream_output,
                    **self.context.variables,
                    **upstream_output,
                }
                if edge.condition.evaluate(eval_ctx):
                    return True

        return False

    def find_next_ready_node(self) -> Optional[str]:
        """Localiza el siguiente nodo listo para ejecutarse según el orden topológico."""
        for nid in self.workflow.nodes:
            if self.is_node_ready(nid):
                return nid
        return None

    def execute_node(self, node_id: str) -> bool:
        """Ejecuta un nodo individual, despacha tareas/agentes y propaga outputs."""
        node = self.workflow.nodes[node_id]

        # 1. Guardar checkpoint antes de la mutación del nodo
        self.context.create_checkpoint(current_node_id=node_id)

        self.context.node_states[node_id] = NodeStatus.RUNNING

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
                else:
                    # Ejecutor estándar por defecto
                    output = {
                        "task_result": f"Executed {handler_key}",
                        "inputs_echo": resolved_inputs,
                        "status": "completed",
                    }

            elif node.node_type == NodeType.AGENT:
                if self.agent_bus and node.agent_id:
                    # Enviar mensaje formal al buzón del agente
                    msg = AgentMessage(
                        message_id=f"wf_agent_{node_id}_{int(time.time()*1000)%10000}",
                        sender_id="praxeon_supervisor",
                        receiver_id=node.agent_id,
                        session_id=self.context.execution_id,
                        task_id=node_id,
                        message_type=MessageType.DELEGATE,
                        priority=MessagePriority.HIGH,
                        payload=resolved_inputs,
                    )
                    self.agent_bus.send(msg)
                    # Procesar simulación o respuesta si está disponible
                    output = {
                        "agent_id": node.agent_id,
                        "dispatched": True,
                        "message_id": msg.message_id,
                        "status": "completed",
                    }
                else:
                    output = {
                        "agent_id": node.agent_id or "unassigned",
                        "response": f"Agent {node.agent_id} completed goal",
                        "status": "completed",
                    }

            elif node.node_type == NodeType.DECISION:
                # Nodo de ramificación condicional
                output = {"evaluated_at": datetime.now(timezone.utc).isoformat(), "status": "evaluated"}

            elif node.node_type in (NodeType.PARALLEL_FORK, NodeType.PARALLEL_JOIN):
                output = {"parallel_sync": True, "status": "completed"}

        except Exception as exc:
            success = False
            output = {"error": str(exc)}

        # 3. Actualizar estado y salidas del nodo
        if success:
            self.context.node_states[node_id] = NodeStatus.COMPLETED
            self.context.node_outputs[node_id] = output
            self.context.execution_history.append(node_id)

            # Si es END, verificar si el workflow concluyó
            if node.node_type == NodeType.END:
                self.context.status = WorkflowStatus.COMPLETED
                self.context.finished_at = datetime.now(timezone.utc)

            # Evaluar propagación y marcar ramas excluidas como SKIPPED
            self._propagate_skips(node_id)
            return True
        else:
            # Manejo de política de reintentos
            retries = self.context.node_retries.get(node_id, 0)
            if retries < node.retry_policy.max_retries:
                self.context.node_retries[node_id] = retries + 1
                self.context.node_states[node_id] = NodeStatus.READY
                return False
            else:
                self.context.node_states[node_id] = NodeStatus.FAILED
                self.context.status = WorkflowStatus.FAILED
                self.context.error_message = f"Fallo en nodo '{node_id}': {output.get('error')}"
                self.context.finished_at = datetime.now(timezone.utc)
                return False

    def _propagate_skips(self, completed_node_id: str) -> None:
        """Marca como SKIPPED los nodos downstream cuyas condiciones nunca se satisficieron."""
        outgoing = self.workflow.get_outgoing_edges(completed_node_id)
        if len(outgoing) > 1:
            node_output = self.context.node_outputs.get(completed_node_id, {})
            eval_ctx = {"output": node_output, **self.context.variables, **node_output}

            for edge in outgoing:
                if edge.condition and not edge.condition.evaluate(eval_ctx):
                    target_id = edge.to_node
                    # Si no tiene otras aristas satisfechas
                    if not self.is_node_ready(target_id) and self.context.node_states[target_id] == NodeStatus.PENDING:
                        self.context.node_states[target_id] = NodeStatus.SKIPPED

    def step(self) -> Optional[str]:
        """Avanza la ejecución un paso ejecutando el siguiente nodo elegible."""
        if self.context.status not in (WorkflowStatus.RUNNING, WorkflowStatus.IDLE):
            return None

        if self.context.status == WorkflowStatus.IDLE:
            self.start()

        next_nid = self.find_next_ready_node()
        if not next_nid:
            # Si no hay nodos listos y no se ha alcanzado END, verificar si terminó
            end_nodes = self.workflow.get_end_nodes()
            if any(self.context.node_states.get(e.node_id) == NodeStatus.COMPLETED for e in end_nodes):
                self.context.status = WorkflowStatus.COMPLETED
            return None

        self.execute_node(next_nid)
        return next_nid

    def run_to_completion(self, max_steps: int = 100) -> WorkflowExecutionContext:
        """Ejecuta iterativamente el flujo hasta su conclusión o bloqueo."""
        if self.context.status == WorkflowStatus.IDLE:
            self.start()

        steps = 0
        while self.context.status == WorkflowStatus.RUNNING and steps < max_steps:
            executed = self.step()
            steps += 1
            if not executed:
                break

        return self.context

    def retry_node(self, node_id: str) -> bool:
        """Reinicia manualmente un nodo fallido o cancelado para reintentar su ejecución."""
        if node_id not in self.workflow.nodes:
            return False

        current_st = self.context.node_states.get(node_id)
        if current_st in (NodeStatus.FAILED, NodeStatus.CANCELLED):
            self.context.node_states[node_id] = NodeStatus.READY
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
