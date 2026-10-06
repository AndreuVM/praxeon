"""Servicio Modular de Orquestación y Edición de Workflows (F6).

Coordina:
- Edición y modelado de flujos visuales (WorkflowEditorService).
- Instanciación y control de ejecuciones (WorkflowExecution, WorkflowEngine).
- Persistencia de estados y retroceso determinista (backtrack).
"""

from typing import Any, Dict, List, Optional

from praxeon.agents.bus import AgentMessageBus
from praxeon.persistence.sqlite_store import SqlitePersistenceStore
from praxeon.runtime.event_bus import EventBus
from praxeon.workflows.editor_service import WorkflowEditorService
from praxeon.workflows.engine import WorkflowEngine
from praxeon.workflows.models import (
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowStatus,
)
from praxeon.workflows.scheduler import WorkflowScheduler


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
        self.scheduler = WorkflowScheduler(persistence_store=self.persistence_store)
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
        engine = WorkflowEngine(wf, agent_bus=self.agent_bus)
        engine.start(initial_variables=initial_variables)
        exec_snapshot = engine.get_execution()
        if self.persistence_store:
            self.persistence_store.save_workflow_execution(exec_snapshot)
        if register_in_scheduler:
            self.scheduler.register_engine(engine)
        return exec_snapshot

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
