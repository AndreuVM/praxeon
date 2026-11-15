"""Raíz de composición del servidor (auditoría A-03).

Aquí, y solo aquí, se decide qué implementación concreta usa cada colaborador del ``RuntimeApplicationService``
(almacenes SQLite, motor de políticas, ejecutor, supervisor por defecto, agentes, workflows y servicios de dominio) y
cuál es el perfil de seguridad del proceso. El servicio recibe los colaboradores ya construidos y solo los orquesta.

- ``resolve_security_profile``: el perfil (``dev`` o ``production``) a partir del argumento o del entorno.
- ``build_components``: los colaboradores, con sustituciones opcionales (tests, app de escritorio).
- ``build_runtime_service``: el servicio completo.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from praxeon.agents.bus import AgentMessageBus
from praxeon.agents.registry import AgentRegistry
from praxeon.agents.templates import AgentTemplateCatalog
from praxeon.config import PraxeonConfig, default_config, resolve_cache_dir
from praxeon.context.manager import ContextManager
from praxeon.integrations.mcp_client import McpConfigStore, McpManager
from praxeon.policy.engine import PolicyEngine
from praxeon.policy.registry import ToolRegistry
from praxeon.providers.base import BaseReasoningProvider
from praxeon.providers.laya import LayaProvider
from praxeon.providers.mock import MockProvider
from praxeon.providers.replay import ReplayProvider
from praxeon.providers.typesafe import TypeSafeAdapter
from praxeon.reasoning.classifier import CommandClassifier
from praxeon.reasoning.evidence import EvidenceEngine
from praxeon.reasoning.risk import RiskEngine
from praxeon.routing.router import AgentRouter
from praxeon.runtime.adaptive.runtime import AdaptiveAgentRuntime
from praxeon.runtime.child_env import full_access_enabled
from praxeon.runtime.decision_store import SqliteDecisionRepository
from praxeon.runtime.event_bus import EventBus, EventStore
from praxeon.runtime.executor import SecureExecutor
from praxeon.runtime.keys import production_keys_problem, receipt_key
from praxeon.runtime.nonce_store import NonceStore, SqliteNonceStore
from praxeon.runtime.state_store import SqliteStateStore
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
from praxeon.webtools import WebSettingsStore, WebTools
from praxeon.workflows.editor_service import WorkflowEditorService

if TYPE_CHECKING:
    from praxeon.server.dependencies import RuntimeApplicationService

logger = logging.getLogger("praxeon.server.dependencies")

DEFAULT_AGENT_TEMPLATES = ("developer", "security_auditor", "researcher", "writer", "code_reviewer")


def resolve_security_profile(profile: str | None = None) -> str:
    """Perfil de seguridad del proceso: el indicado o ``PRAXEON_PROFILE``/``PRAXEON_ENV``; ``dev`` por defecto."""
    return (profile or os.environ.get("PRAXEON_PROFILE") or os.environ.get("PRAXEON_ENV") or "dev").lower().strip()


@dataclass
class RuntimeComponents:
    """Colaboradores del servicio de aplicación, ya construidos y conectados entre sí."""

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
    default_provider: Any
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


def default_supervisor(config: PraxeonConfig) -> Any:
    """Supervisor semántico por defecto según la configuración (LAYA, TypeSafe con API, replay o mock)."""
    name = config.provider.name.lower()
    if name in ("mock", "mockprovider", "test", "testing"):
        return MockProvider()
    if name in ("replay", "replayprovider"):
        return ReplayProvider()
    if name == "laya":
        return LayaProvider(backend=config.provider.laya_backend)
    if config.provider.use_api and config.provider.api_key:
        return TypeSafeAdapter(api_key=config.provider.api_key, model_name=config.provider.model)
    return MockProvider()


def _receipt_secret(security_profile: str) -> str:
    """Clave de recibos propia (explícita o derivada del secreto maestro); sin ninguna, una aleatoria por proceso.

    En producción las claves son obligatorias: sin ellas el servicio no arranca.
    """
    if security_profile == "production":
        problem = production_keys_problem()
        if problem:
            raise ValueError(problem)
    return receipt_key() or secrets.token_hex(32)


def _agent_registry(db_dir: str) -> AgentRegistry:
    """Registro de agentes; en la primera ejecución se siembran los agentes de plantilla."""
    registry = AgentRegistry(db_path=os.path.join(db_dir, "persistence.db"))
    if not registry.list_all():
        for template in DEFAULT_AGENT_TEMPLATES:
            try:
                registry.register(AgentTemplateCatalog.instantiate(template, f"ag_{template}"))
            except Exception as exc:
                # No impide arrancar, pero el operador debe saber por qué falta un agente por defecto (F-05)
                logger.warning("No se pudo registrar el agente por defecto '%s': %s", template, exc)
    return registry


def build_components(
    *,
    config: PraxeonConfig | None = None,
    db_dir: str | None = None,
    security_profile: str | None = None,
    event_bus: EventBus | None = None,
    state_store: SqliteStateStore | None = None,
    nonce_store: NonceStore | None = None,
    decision_repository: SqliteDecisionRepository | None = None,
    agent_bus: AgentMessageBus | None = None,
    agent_registry: AgentRegistry | None = None,
    agent_router: AgentRouter | None = None,
    context_manager: ContextManager | None = None,
    workflow_editor_service: WorkflowEditorService | None = None,
    adaptive_runtime: AdaptiveAgentRuntime | None = None,
    provider: BaseReasoningProvider | None = None,
) -> RuntimeComponents:
    """Construye y conecta los colaboradores del servicio. Lo que se pase ya construido se usa tal cual."""
    cfg = config or default_config
    directory = resolve_cache_dir(db_dir)
    os.makedirs(directory, exist_ok=True)
    profile = resolve_security_profile(security_profile)

    def db(name: str) -> str:
        return os.path.join(directory, name)

    bus = event_bus or EventBus(store=EventStore(db_path=db("events.db")))
    states = state_store or SqliteStateStore(db_path=db("state.db"))
    nonces = nonce_store or SqliteNonceStore(db_path=db("nonces.db"))
    decisions = decision_repository or SqliteDecisionRepository(db_path=db("decisions.db"))

    # El motor de políticas comparte el registro con el ejecutor: lo que se registra (herramientas MCP) lo conocen ambos
    tools = ToolRegistry(register_defaults=True)
    policy_engine = PolicyEngine(secret_key=_receipt_secret(profile), registry=tools)
    executor = SecureExecutor(
        registry=tools,
        secret_key=policy_engine.secret_key,
        nonce_store=nonces,
        # Full Access solo si el proceso lo habilita: la app de escritorio sí; servidor y CLI no (PRAXEON_ALLOW_FULL_ACCESS)
        allow_full_access=full_access_enabled(),
    )
    # Herramientas de servidores MCP y acceso web de solo lectura: en el mismo ToolRegistry/Executor que las nativas
    mcp = McpManager(McpConfigStore(), tools, executor)
    web = WebTools(tools, executor, WebSettingsStore())
    web.register()
    risk_engine = RiskEngine(registry=tools)
    classifier = CommandClassifier()
    evidence_engine = EvidenceEngine()
    supervisor = provider if provider is not None else default_supervisor(cfg)

    context = context_manager or ContextManager()
    agents_bus = agent_bus or AgentMessageBus()
    agents = agent_registry or _agent_registry(directory)
    router = agent_router or AgentRouter(registry=agents, message_bus=agents_bus, llm_provider=supervisor, event_bus=bus)
    persistence = getattr(agents, "store", None)
    editor = workflow_editor_service or WorkflowEditorService(event_bus=bus, agent_bus=agents_bus, persistence_store=persistence)
    adaptive = adaptive_runtime or AdaptiveAgentRuntime(
        registry=agents, router=router, context_manager=context, bus=agents_bus, event_store=getattr(bus, "store", None)
    )

    return RuntimeComponents(
        config=cfg,
        db_dir=directory,
        security_profile=profile,
        event_bus=bus,
        state_store=states,
        nonce_store=nonces,
        decision_repository=decisions,
        registry=tools,
        policy_engine=policy_engine,
        executor=executor,
        mcp=mcp,
        skills=SkillStore(),
        web=web,
        risk_engine=risk_engine,
        command_classifier=classifier,
        evidence_engine=evidence_engine,
        default_provider=supervisor,
        context_manager=context,
        agent_bus=agents_bus,
        agent_registry=agents,
        agent_router=router,
        persistence_store=persistence,
        workflow_editor_service=editor,
        adaptive_runtime=adaptive,
        auth_service=AuthService(revocation_db_path=db("auth_revocations.db")),
        session_service=SessionService(event_bus=bus, state_store=states),
        agent_service=AgentService(registry=agents, router=router, bus=agents_bus),
        workflow_service=WorkflowService(event_bus=bus, agent_bus=agents_bus, editor_service=editor, persistence_store=persistence),
        execution_service=ExecutionService(),
        mission_service=MissionService(),
        decision_service=DecisionService(
            event_bus=bus,
            decision_repository=decisions,
            policy_engine=policy_engine,
            risk_engine=risk_engine,
            evidence_engine=evidence_engine,
            command_classifier=classifier,
        ),
    )


def build_runtime_service(security_profile: str | None = None, **overrides: Any) -> RuntimeApplicationService:
    """El servicio de aplicación completo, con sus colaboradores construidos aquí."""
    from praxeon.server.dependencies import RuntimeApplicationService

    return RuntimeApplicationService(components=build_components(security_profile=security_profile, **overrides))
