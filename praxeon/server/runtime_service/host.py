"""Lo que los mixins del servicio esperan encontrar en ``RuntimeApplicationService`` (AUD-23).

``DecisionPipelineMixin`` y ``MissionRuntimeMixin`` usan ``self.state_store``, ``self._lock``, ``self.event_bus``…, que define
el ``__init__`` del servicio al copiarlos de ``RuntimeComponents``. Sin esta declaración mypy no puede saber que existen.

``RuntimeHost`` solo tiene anotaciones: no asigna nada ni importa nada en ejecución (todo bajo ``TYPE_CHECKING``). Los tipos
son los de ``praxeon.server.bootstrap.RuntimeComponents``; si allí cambia uno, mypy lo señala aquí.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from praxeon.agents.bus import AgentMessageBus
    from praxeon.agents.registry import AgentRegistry
    from praxeon.config import PraxeonConfig
    from praxeon.context.manager import ContextManager
    from praxeon.integrations.mcp_client import McpManager
    from praxeon.policy.engine import PolicyEngine
    from praxeon.policy.registry import ToolRegistry
    from praxeon.reasoning.classifier import CommandClassifier
    from praxeon.reasoning.evidence import EvidenceEngine
    from praxeon.reasoning.risk import RiskEngine
    from praxeon.routing.router import AgentRouter
    from praxeon.runtime.adaptive.runtime import AdaptiveAgentRuntime
    from praxeon.runtime.decision_store import SqliteDecisionRepository
    from praxeon.runtime.event_bus import EventBus
    from praxeon.runtime.executor import SecureExecutor
    from praxeon.runtime.nonce_store import NonceStore
    from praxeon.runtime.session_runtime import SessionRuntime
    from praxeon.runtime.state_store import SqliteStateStore
    from praxeon.server.coordinators import (
        CheckpointCoordinator,
        DiagnosticsCoordinator,
        SessionLifecycleCoordinator,
        StepExecutionCoordinator,
    )
    from praxeon.server.schemas.action import ProposeActionRequest
    from praxeon.server.schemas.decision import ConfirmDecisionResponse, DecisionResponse, ExecuteDecisionResponse
    from praxeon.server.services import (
        AgentService,
        AuthService,
        DecisionService,
        ExecutionService,
        MissionService,
        SessionService,
        WorkflowService,
    )
    from praxeon.skills import SkillStore
    from praxeon.webtools import WebTools
    from praxeon.workflows.editor_service import WorkflowEditorService


class RuntimeHost:
    """Atributos del servicio que usan los mixins. Solo anotaciones: no hay valores ni código."""

    config: PraxeonConfig
    db_dir: str
    security_profile: str

    event_bus: EventBus
    state_store: SqliteStateStore
    nonce_store: NonceStore
    decision_repository: SqliteDecisionRepository

    registry: ToolRegistry
    policy_engine: PolicyEngine
    executor: SecureExecutor
    mcp: McpManager
    skills: SkillStore
    web: WebTools
    risk_engine: RiskEngine
    command_classifier: CommandClassifier
    evidence_engine: EvidenceEngine
    _default_provider: Any

    _session_runtimes: dict[str, SessionRuntime]
    _lock: threading.Lock
    _sessions_meta: dict[str, dict[str, Any]]
    _decisions: dict[str, dict[str, Any]]
    _running_missions: dict[str, dict[str, Any]]
    _mission_threads: dict[str, threading.Thread]   # hilo de cada misión (límite de misiones concurrentes, FE-008)
    _missions_starting: int                         # misiones que están arrancando y aún no tienen hilo

    context_manager: ContextManager
    agent_bus: AgentMessageBus
    agent_registry: AgentRegistry
    agent_router: AgentRouter
    persistence_store: Any
    workflow_editor_service: WorkflowEditorService
    adaptive_runtime: AdaptiveAgentRuntime

    auth_service: AuthService
    session_service: SessionService
    agent_service: AgentService
    workflow_service: WorkflowService
    execution_service: ExecutionService
    mission_service: MissionService
    decision_service: DecisionService

    session_coordinator: SessionLifecycleCoordinator
    step_coordinator: StepExecutionCoordinator
    checkpoint_coordinator: CheckpointCoordinator
    diagnostics_coordinator: DiagnosticsCoordinator

    if TYPE_CHECKING:
        # Métodos que un mixin llama y define otro (o el propio servicio). Las firmas se comprueban contra las reales:
        # si una cambia, mypy lo señala. En ejecución no existen.
        def create_session(
            self,
            goal: str,
            session_id: str | None = None,
            agent_name: str = "CodingAgent",
            metadata: dict[str, Any] | None = None,
            execution_mode: str | None = None,
            workspace_root: str | None = None,
        ) -> dict[str, Any]: ...

        def propose_action(self, session_id: str, proposal: ProposeActionRequest) -> DecisionResponse: ...

        def confirm_decision(
            self,
            decision_id: str,
            approved: bool,
            reason: str | None = None,
            actor: str = "human_operator",
            operator_id: str | None = None,
            role: str = "operator",
            operator_token: str | None = None,
            caller_is_verified_operator: bool = False,
            security_profile: str | None = None,
        ) -> ConfirmDecisionResponse: ...

        def execute_decision(
            self,
            decision_id: str,
            capability_token: dict[str, Any] | None = None,
            operator_id: str | None = None,
            role: str = "operator",
        ) -> ExecuteDecisionResponse: ...
