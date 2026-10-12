"""Servicio Modular de Orquestación y Edición de Workflows (F6).

Coordina:
- Edición y modelado de flujos visuales (WorkflowEditorService).
- Instanciación y control de ejecuciones (WorkflowExecution, WorkflowEngine).
- Persistencia de estados y retroceso determinista (backtrack).
"""

import logging
from typing import Any, Dict, List, Optional

from praxeon.agents.bus import AgentMessageBus
from praxeon.persistence.sqlite_store import SqlitePersistenceStore
from praxeon.domain.events import EventType
from praxeon.runtime.event_bus import EventBus
from praxeon.workflows.editor_service import WorkflowEditorService
from praxeon.workflows.engine import WorkflowEngine
from praxeon.workflows.models import (
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowStatus,
)
from praxeon.workflows.scheduler import WorkflowScheduler


logger = logging.getLogger(__name__)


class WorkflowService:
    """Gestiona el diseño, ejecución, persistencia y scheduling de flujos de trabajo."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        agent_bus: Optional[AgentMessageBus] = None,
        editor_service: Optional[WorkflowEditorService] = None,
        persistence_store: Optional[SqlitePersistenceStore] = None,
        enable_scheduler: bool = False,
    ):
        self.event_bus = event_bus
        self.agent_bus = agent_bus
        self.persistence_store = persistence_store
        self.editor_service = editor_service or WorkflowEditorService(
            event_bus=event_bus,
            agent_bus=agent_bus,
            persistence_store=persistence_store,
        )
        self.scheduler = WorkflowScheduler(persistence_store=self.persistence_store, event_bus=event_bus)
        # Motores que avanzan fuera del scheduler (misiones gobernadas por workflow): aprobar/cancelar siguen funcionando
        self._external_engines: Dict[str, WorkflowEngine] = {}
        if enable_scheduler:
            self.scheduler.start()

    def get_editor_service(self) -> WorkflowEditorService:
        return self.editor_service

    def get_scheduler(self) -> WorkflowScheduler:
        return self.scheduler

    def create_workflow(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        self.editor_service.register_workflow(definition)
        return definition

    def delete_workflow(self, workflow_id: str) -> bool:
        return self.editor_service.delete_workflow(workflow_id)

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        return self.editor_service.get_workflow(workflow_id)

    def list_workflows(self) -> List[Dict[str, Any]]:
        return self.editor_service.list_workflows()

    def run_workflow_execution(
        self,
        workflow_id: str,
        initial_variables: Optional[Dict[str, Any]] = None,
        register_in_scheduler: bool = True,
    ) -> Optional[WorkflowExecution]:
        """Inicia una nueva instancia de ejecución para el workflow especificado."""
        wf = self.get_workflow(workflow_id)
        if not wf:
            return None
        # Mismas garantías que la ruta interactiva: supervisión real y simulación solo si es explícita
        simulate = self.editor_service.allow_simulation
        engine = WorkflowEngine(
            wf,
            agent_bus=self.agent_bus,
            allow_synthetic_fallback=simulate,
            supervised_runner=None if simulate else self.editor_service.supervised_runner,
        )
        engine.start(initial_variables=initial_variables)
        exec_snapshot = engine.get_execution()
        if self.persistence_store:
            self.persistence_store.save_workflow_execution(exec_snapshot)
        if register_in_scheduler:
            self.scheduler.register_engine(engine)
            self.scheduler.start()  # idempotente: avanza, reintenta y aplica timeouts sin polling del cliente
        return exec_snapshot

    def register_external_engine(self, engine: WorkflowEngine) -> None:
        self._external_engines[engine.context.execution_id] = engine

    def unregister_external_engine(self, execution_id: str) -> None:
        self._external_engines.pop(execution_id, None)

    def sync_engine(self, engine: WorkflowEngine) -> None:
        self.scheduler.sync_engine(engine)

    FAILURE_ACTIONS = ("retry", "skip", "abort")

    def announce_failure(self, engine: WorkflowEngine, node_id: str, node_name: str, error: str) -> None:
        """Avisa (notificación de escritorio + UI) de que un nodo falló y la misión espera la decisión del operador."""
        if self.event_bus is None:
            return
        exec_id = engine.context.execution_id
        try:
            self.event_bus.emit(
                session_id=exec_id[len("wfx-"):] if exec_id.startswith("wfx-") else exec_id,
                event_type=EventType.APPROVAL_REQUESTED,
                node_id=node_id,
                payload={
                    "source": "workflow",
                    "kind": "node_failure",
                    "workflow_id": engine.workflow.workflow_id,
                    "execution_id": exec_id,
                    "node_name": node_name,
                    "prompt": f"El nodo «{node_name}» falló: {error[:300]}. ¿Reintentar, omitir o abortar?",
                    "options": list(self.FAILURE_ACTIONS),
                },
            )
        except Exception:
            logger.warning("No se pudo publicar el aviso de fallo de nodo", exc_info=True)

    def resolve_failed_node(self, execution_id: str, node_id: str, action: str) -> Optional[bool]:
        """Decisión del operador sobre un nodo fallido de una misión con workflow: retry | skip | abort.

        None si la ejecución no está activa; False si el nodo no está esperando una decisión.
        """
        if action not in self.FAILURE_ACTIONS:
            raise ValueError(f"Acción inválida '{action}'. Usa una de: {', '.join(self.FAILURE_ACTIONS)}.")
        engine = self._external_engines.get(execution_id)
        if engine is None:
            return None
        with engine._lock:
            pending = engine.context.variables.get("_pending_failure")
            if not pending or pending.get("node_id") != node_id:
                return False
            engine.context.variables["_failure_decision"] = {"node_id": node_id, "action": action}
        return True

    def _active_engine(self, execution_id: str) -> Optional[WorkflowEngine]:
        return self._external_engines.get(execution_id) or self.scheduler.get_active_engines().get(execution_id)

    def cancel_execution(self, execution_id: str, reason: str = "Cancelada por el operador") -> Optional[WorkflowExecution]:
        """Cancela una ejecución activa (no altera una ya terminada)."""
        engine = self._active_engine(execution_id)
        if engine is None:
            return self.get_execution(execution_id)
        engine.cancel(reason)
        snapshot = engine.get_execution()
        self.save_execution(snapshot)
        return snapshot

    def approve_execution_node(
        self, execution_id: str, node_id: str, approved: bool = True, comment: str = "", approver_id: str = "operator"
    ) -> Optional[bool]:
        """Aprueba o rechaza un nodo en espera de una ejecución activa. None si la ejecución no está activa."""
        engine = self._active_engine(execution_id)
        if engine is None:
            return None
        ok = engine.approve_node(node_id, approved=approved, comment=comment, approver_id=approver_id)
        self.save_execution(engine.get_execution())
        return ok

    def save_execution(self, execution: WorkflowExecution) -> None:
        if self.persistence_store:
            self.persistence_store.save_workflow_execution(execution)

    def get_execution(self, execution_id: str) -> Optional[WorkflowExecution]:
        if self.persistence_store:
            return self.persistence_store.get_workflow_execution(execution_id)
        return None

    def list_executions(
        self,
        workflow_id: Optional[str] = None,
        status: Optional[WorkflowStatus] = None,
    ) -> List[WorkflowExecution]:
        if self.persistence_store:
            return self.persistence_store.list_workflow_executions(
                workflow_id=workflow_id,
                status=status,
            )
        return []
