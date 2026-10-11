"""Capa de servicios de aplicación y dependencias para el Web Server (praxeon/server/dependencies.py).

Especificación PRAXEON 1.0 (Sección 2.1 y Sección 8).
La API Web nunca ejecuta comandos directamente ni contiene reglas de seguridad
paralelas. Toda operación pasa por el pipeline canónico del runtime:
Proposal -> Evidence -> Risk -> Provider -> Policy -> Capability -> Execution
"""

from __future__ import annotations

from datetime import datetime, timezone


import logging
import os
import secrets
import threading
from typing import Any, Dict, List, Optional
import uuid

logger = logging.getLogger("praxeon.server.dependencies")

from praxeon.config import PraxeonConfig, default_config, resolve_cache_dir
from praxeon.domain.events import EventType, RuntimeEvent
from praxeon.domain.models import (
    Goal,
)
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
from praxeon.runtime.decision_store import SqliteDecisionRepository
from praxeon.runtime.event_bus import EventBus, EventStore
from praxeon.runtime.executor import SecureExecutor
from praxeon.runtime.nonce_store import NonceStore, SqliteNonceStore
from praxeon.context.manager import ContextManager
from praxeon.agents.bus import AgentMessageBus
from praxeon.agents.registry import AgentRegistry
from praxeon.agents.templates import AgentTemplateCatalog
from praxeon.routing.router import AgentRouter
from praxeon.workflows.editor_service import WorkflowEditorService
from praxeon.runtime.adaptive.runtime import AdaptiveAgentRuntime
from praxeon.runtime.sandbox import (
    get_default_workspace_root,
)
from praxeon.domain.decision_provider import DecisionModelConfig
from praxeon.providers.registry import default_registry
from praxeon.runtime.decision_runtime import DecisionRuntime
from praxeon.runtime.session_runtime import SessionRuntime
from praxeon.runtime.state import SessionState
from praxeon.runtime.state_store import SqliteStateStore
from praxeon.runtime.tree_reducer import reduce_events_to_tree
from praxeon.server.services import (
    AgentService,
    AuthService,
    DecisionService,
    ExecutionService,
    MissionService,
    SessionService,
    WorkflowService,
    generate_goal_tailored_steps,
)


from praxeon.server.runtime_service.decision_pipeline import DecisionPipelineMixin  # noqa: E402
from praxeon.server.runtime_service.mission_runtime import MissionRuntimeMixin  # noqa: E402


class RuntimeApplicationService(DecisionPipelineMixin, MissionRuntimeMixin):
    """Servicio central de aplicación que orquesta el ciclo de supervisión para la API Web."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        state_store: Optional[SqliteStateStore] = None,
        nonce_store: Optional[NonceStore] = None,
        decision_repository: Optional[SqliteDecisionRepository] = None,
        config: Optional[PraxeonConfig] = None,
        db_dir: Optional[str] = None,
        agent_bus: Optional[AgentMessageBus] = None,
        agent_registry: Optional[AgentRegistry] = None,
        agent_router: Optional[AgentRouter] = None,
        context_manager: Optional[ContextManager] = None,
        workflow_editor_service: Optional[WorkflowEditorService] = None,
        adaptive_runtime: Optional[AdaptiveAgentRuntime] = None,
        provider: Optional[BaseReasoningProvider] = None,
    ):
        self.config = config or default_config
        self.db_dir = resolve_cache_dir(db_dir)
        os.makedirs(self.db_dir, exist_ok=True)


        self.event_bus = event_bus or EventBus(
            store=EventStore(db_path=os.path.join(self.db_dir, "events.db"))
        )
        self.state_store = state_store or SqliteStateStore(
            db_path=os.path.join(self.db_dir, "state.db")
        )
        self.nonce_store = nonce_store or SqliteNonceStore(
            db_path=os.path.join(self.db_dir, "nonces.db")
        )
        self.decision_repository = decision_repository or SqliteDecisionRepository(
            db_path=os.path.join(self.db_dir, "decisions.db")
        )


        self.registry = ToolRegistry(register_defaults=True)
        prof = (os.environ.get("PRAXEON_PROFILE") or os.environ.get("PRAXEON_ENV") or "dev").lower().strip()
        secret_env = os.environ.get("PRAXEON_SECRET_KEY")
        insecure_defaults = {"", "praxeon_secret_hmac_key_v1", "default", "secret", "change_me"}
        if prof == "production":
            if not secret_env or secret_env in insecure_defaults or len(secret_env) < 32:
                raise ValueError(
                    "Perfil de seguridad 'production' requiere que PRAXEON_SECRET_KEY esté configurada con al menos 32 caracteres y no use claves por defecto."
                )
            effective_secret = secret_env
        else:
            effective_secret = secret_env or secrets.token_hex(32)

        self.policy_engine = PolicyEngine(secret_key=effective_secret)
        self.executor = SecureExecutor(
            registry=self.registry,
            secret_key=self.policy_engine.secret_key,
            nonce_store=self.nonce_store,
            allow_full_access=True,
        )
        self.risk_engine = RiskEngine()
        self.command_classifier = CommandClassifier()
        self.evidence_engine = EvidenceEngine()

        # Configurar proveedor supervisor semántico por defecto
        if provider is not None:
            self._default_provider = provider
        elif self.config.provider.name.lower() in ("mock", "mockprovider", "test", "testing"):
            self._default_provider = MockProvider()
        elif self.config.provider.name.lower() in ("replay", "replayprovider"):
            self._default_provider = ReplayProvider()
        elif self.config.provider.name.lower() == "laya":
            self._default_provider = LayaProvider(backend=self.config.provider.laya_backend)
        elif self.config.provider.use_api and self.config.provider.api_key:
            self._default_provider = TypeSafeAdapter(
                api_key=self.config.provider.api_key,
                model_name=self.config.provider.model,
            )
        else:
            self._default_provider = MockProvider()

        self._session_runtimes: Dict[str, SessionRuntime] = {}

        self._lock = threading.Lock()
        self._sessions_meta: Dict[str, Dict[str, Any]] = {}
        self._decisions: Dict[str, Dict[str, Any]] = {}
        self._running_missions: Dict[str, Dict[str, Any]] = {}

        # ---------------------------------------------------------------------
        # SUBSISTEMAS UNIFICADOS DE AGENTES, CONTEXTO, WORKFLOWS Y ENRUTAMIENTO
        # ---------------------------------------------------------------------
        self.context_manager = context_manager or ContextManager()
        self.agent_bus = agent_bus or AgentMessageBus()
        self.agent_registry = agent_registry or AgentRegistry(db_path=os.path.join(self.db_dir, "persistence.db"))
        if not self.agent_registry.list_all():
            for tpl in ["developer", "security_auditor", "researcher", "writer", "code_reviewer"]:
                try:
                    self.agent_registry.register(AgentTemplateCatalog.instantiate(tpl, f"ag_{tpl}"))
                except Exception:
                    pass
        self.agent_router = agent_router or AgentRouter(
            registry=self.agent_registry,
            message_bus=self.agent_bus,
            llm_provider=self.provider,
            event_bus=self.event_bus,
        )
        self.persistence_store = getattr(self.agent_registry, "store", None)
        self.workflow_editor_service = workflow_editor_service or WorkflowEditorService(
            event_bus=self.event_bus,
            agent_bus=self.agent_bus,
            persistence_store=self.persistence_store,
        )
        self.adaptive_runtime = adaptive_runtime or AdaptiveAgentRuntime(
            registry=self.agent_registry,
            router=self.agent_router,
            context_manager=self.context_manager,
            bus=self.agent_bus,
            event_store=getattr(self.event_bus, "store", None),
        )

        # ---------------------------------------------------------------------
        # SERVICIOS MODULARES ESPECIALIZADOS
        # ---------------------------------------------------------------------
        self.auth_service = AuthService()
        self.session_service = SessionService(event_bus=self.event_bus, state_store=self.state_store)
        self.agent_service = AgentService(registry=self.agent_registry, router=self.agent_router, bus=self.agent_bus)
        self.workflow_service = WorkflowService(
            event_bus=self.event_bus,
            agent_bus=self.agent_bus,
            editor_service=self.workflow_editor_service,
            persistence_store=self.persistence_store,
        )
        self.execution_service = ExecutionService()
        self.mission_service = MissionService()
        self.decision_service = DecisionService(
            event_bus=self.event_bus,
            decision_repository=self.decision_repository,
            policy_engine=self.policy_engine,
            risk_engine=self.risk_engine,
            evidence_engine=self.evidence_engine,
            command_classifier=self.command_classifier,
        )

        # REC-01: Reconciliación transaccional automática de misiones tras reinicio del proceso
        if hasattr(self.state_store, "reconcile_interrupted_missions"):
            try:
                recovered = self.state_store.reconcile_interrupted_missions()
                if recovered:
                    logger.info("[RECOVERY] Reconciliadas %d misiones interrumpidas tras reinicio de PRAXEON.", len(recovered))
                    for m in recovered:
                        sid_rec = m.get("session_id")
                        if sid_rec:
                            with self._lock:
                                self._running_missions[sid_rec] = m
            except Exception as ex:
                logger.warning("Fallo durante la reconciliación de misiones tras reinicio: %s", ex)

        # ---------------------------------------------------------------------
        # COORDINADORES MODULARES DE DOMINIO (REF-02)
        # ---------------------------------------------------------------------
        from praxeon.server.coordinators import (
            SessionLifecycleCoordinator,
            StepExecutionCoordinator,
            CheckpointCoordinator,
            DiagnosticsCoordinator,
        )
        self.session_coordinator = SessionLifecycleCoordinator(self)
        self.step_coordinator = StepExecutionCoordinator(self)
        self.checkpoint_coordinator = CheckpointCoordinator(self)
        self.diagnostics_coordinator = DiagnosticsCoordinator(self)

    @property
    def provider(self) -> Any:
        """Acceso al proveedor por defecto preservando compatibilidad retroactiva."""
        return self._default_provider

    @provider.setter
    def provider(self, value: Any) -> None:
        """Permite actualizar el proveedor por defecto preservando compatibilidad retroactiva."""
        self._default_provider = value

    def register_session_runtime(
        self,
        session_id: str,
        provider: Any,
        llm_runtime: Optional[Any] = None,
        context_manager: Optional[Any] = None,
        policy_profile: str = "default",
        execution_profile: str = "local_restricted",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionRuntime:
        """Registra un runtime de ejecución y supervisión aislado para una sesión."""
        with self._lock:
            runtime = SessionRuntime(
                session_id=session_id,
                decision_provider=provider,
                llm_runtime=llm_runtime,
                context_manager=context_manager,
                policy_profile=policy_profile,
                execution_profile=execution_profile,
                metadata=metadata or {},
            )
            self._session_runtimes[session_id] = runtime
            return runtime

    def get_session_provider(self, session_id: str) -> Any:
        """Obtiene el proveedor de decisiones exclusivo para la sesión solicitada.
        
        Garantiza que misiones heterogéneas concurrentes no contaminen sus evaluaciones
        semánticas ni compartan un provider global mutable.
        """
        with self._lock:
            if session_id in self._session_runtimes:
                return self._session_runtimes[session_id].decision_provider

        meta: Dict[str, Any] = {}
        with self._lock:
            if session_id in self._sessions_meta:
                meta = dict(self._sessions_meta[session_id].get("metadata") or {})
        if not meta:
            st = self.state_store.load_state(session_id)
            if st and st.metadata:
                meta = dict(st.metadata)

        supervisor = meta.get("supervisor") or meta.get("decision_provider")
        decision_model = meta.get("decision_model")

        cfg: Optional[DecisionModelConfig] = None
        if isinstance(decision_model, DecisionModelConfig):
            cfg = decision_model
        elif isinstance(decision_model, dict):
            try:
                cfg = DecisionModelConfig(**decision_model)
            except Exception as e:
                logger.warning(f"Error parseando decision_model dict: {e}")
        elif supervisor:
            cfg = DecisionModelConfig(provider=str(supervisor), model_id=str(supervisor))

        dec_runtime: Optional[DecisionRuntime] = None
        if cfg:
            try:
                dec_runtime = DecisionRuntime.from_config(cfg, registry=default_registry)
                provider_instance = dec_runtime.provider
            except Exception as e:
                logger.warning(f"Error resolviendo DecisionRuntime para sesión '{session_id}': {e}. Usando fallback.")
                provider_instance = self._default_provider
        else:
            provider_instance = self._default_provider

        with self._lock:
            if session_id not in self._session_runtimes:
                self._session_runtimes[session_id] = SessionRuntime(
                    session_id=session_id,
                    decision_provider=provider_instance,
                    decision_runtime=dec_runtime,
                    metadata=meta,
                )
            return self._session_runtimes[session_id].decision_provider


    # =========================================================================
    # ACCESORES DEL CONTROL PLANE CANÓNICO Y SERVICIOS MODULARES
    # =========================================================================

    def get_auth_service(self) -> AuthService:
        """Retorna el servicio de autenticación y tickets."""
        return self.auth_service

    def get_session_service(self) -> SessionService:
        """Retorna el servicio de sesiones y checkpoints."""
        return self.session_service

    def get_agent_service(self) -> AgentService:
        """Retorna el servicio de agentes y enrutamiento."""
        return self.agent_service

    def get_workflow_service(self) -> WorkflowService:
        """Retorna el servicio de workflows y ejecuciones."""
        return self.workflow_service

    def get_decision_service(self) -> DecisionService:
        """Retorna el servicio de evaluación y persistencia de decisiones."""
        return self.decision_service

    def get_execution_service(self) -> ExecutionService:
        """Retorna el servicio de ejecución en sandbox."""
        return self.execution_service

    def get_mission_service(self) -> MissionService:
        """Retorna el servicio de gestión de misiones."""
        return self.mission_service

    def get_agent_registry(self) -> AgentRegistry:
        """Retorna el registro canónico de agentes del sistema."""
        return self.agent_registry

    def get_agent_router(self) -> AgentRouter:
        """Retorna el enrutador inteligente de tareas a agentes."""
        return self.agent_router

    def get_agent_bus(self) -> AgentMessageBus:
        """Retorna el bus formal de mensajería inter-agente."""
        return self.agent_bus

    def get_events(self, session_id: str) -> List[RuntimeEvent]:
        """Recupera la secuencia ordenada de eventos de una sesión."""
        return self.event_bus.store.get_events(session_id)

    def get_context_manager(self) -> ContextManager:
        """Retorna el gestor de optimización de contexto y caché."""
        return self.context_manager

    def get_workflow_editor_service(self) -> WorkflowEditorService:
        """Retorna el servicio unificado de orquestación y edición de workflows."""
        return self.workflow_editor_service

    def get_adaptive_runtime(self) -> AdaptiveAgentRuntime:
        """Retorna el runtime adaptativo integral."""
        return self.adaptive_runtime

    # =========================================================================
    # GESTIÓN DE SESIONES
    # =========================================================================

    def create_session(
        self,
        goal: str,
        session_id: Optional[str] = None,
        agent_name: str = "CodingAgent",
        metadata: Optional[Dict[str, Any]] = None,
        execution_mode: str = "local_restricted",
        workspace_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Crea formalmente una nueva sesión de supervisión y persiste su estado génesis."""
        sid = session_id or f"s-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)

        meta = dict(metadata or {})

        mode_val = meta.get("execution_mode") or execution_mode
        meta["execution_mode"] = mode_val
        is_autonomous = bool(meta.get("allow_unattended_execution") or meta.get("autonomous") or False)
        meta["autonomous"] = is_autonomous
        meta["allow_unattended_execution"] = is_autonomous
        effective_ws = workspace_root or meta.get("workspace_root") or meta.get("working_directory") or get_default_workspace_root()
        meta["workspace_root"] = effective_ws
        meta["working_directory"] = effective_ws
        meta["network_mode"] = "host" if mode_val == "full_access" else "isolated"
        meta["created_by"] = meta.get("created_by", "system")
        # CHG-02: Eliminar inferencia automática de created_by == 'system'.
        # full_access_authorized_by_operator solo debe ser True si viene explícitamente autorizado por el operador.
        meta["full_access_authorized_by_operator"] = bool(meta.get("full_access_authorized_by_operator", False))


        state = SessionState(session_id=sid, goal=Goal(objective=goal), metadata=meta)
        self.state_store.save_state(state)
        # Checkpoint Génesis canónico
        self.state_store.create_checkpoint(session_id=sid, label="Genesis checkpoint", state=state)

        # Inicializar SessionRuntime exclusivo si se declaró supervisor
        if meta.get("supervisor") or meta.get("decision_provider") or meta.get("decision_model"):
            self.get_session_provider(sid)

        with self._lock:
            self._sessions_meta[sid] = {
                "session_id": sid,
                "goal": goal,
                "agent_name": agent_name,
                "status": "Active",
                "execution_mode": mode_val,
                "created_at": now,
                "updated_at": now,
                "metadata": meta,
            }

        # Emitir eventos canónicos iniciales
        root_node_id = f"root_{sid}"
        self.event_bus.emit(
            session_id=sid,
            event_type=EventType.SESSION_STARTED,
            node_id=root_node_id,
            payload={
                "agent_name": agent_name,
                "status": "Active",
                "label": "Start",
                "execution_mode": mode_val,
                "created_at": now.isoformat(),
            },
        )
        self.event_bus.emit(
            session_id=sid,
            event_type=EventType.GOAL_CREATED,
            node_id=f"goal_{sid}",
            parent_id=root_node_id,
            payload={"goal": goal},
        )

        return self._sessions_meta[sid]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene el resumen y metadatos de una sesión."""
        with self._lock:
            if session_id in self._sessions_meta:
                return dict(self._sessions_meta[session_id])

        # Cargar de SQLite si no está en memoria
        state = self.state_store.load_state(session_id)
        if not state:
            return None

        mode_val = state.metadata.get("execution_mode", "local_restricted")
        meta = {
            "session_id": session_id,
            "goal": state.goal.objective,
            "agent_name": "CodingAgent",
            "status": "Active",
            "execution_mode": mode_val,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "metadata": state.metadata,

        }
        with self._lock:
            self._sessions_meta[session_id] = meta
        return meta

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Devuelve todas las sesiones registradas con sus contadores de decisiones."""
        session_ids = self.state_store.list_sessions()
        # Combinar con IDs en memoria
        with self._lock:
            for s in self._sessions_meta.keys():
                if s not in session_ids:
                    session_ids.append(s)

        results = []
        for sid in session_ids:
            summary = self.get_session_summary(sid)
            if summary:
                results.append(summary)
        return results

    def get_session_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Calcula el resumen agregado (contadores, eventos, nodos) de una sesión."""
        sess = self.get_session(session_id)
        if not sess:
            return None

        events = self.event_bus.get_all_events(session_id)
        total_decisions = sum(1 for e in events if e.type == EventType.POLICY_DECIDED)
        allowed = sum(1 for e in events if e.type == EventType.POLICY_DECIDED and "ALLOW" in str(e.payload.get("status", "")).upper())
        blocked = sum(1 for e in events if e.type == EventType.POLICY_DECIDED and "BLOCK" in str(e.payload.get("status", "")).upper())
        review = sum(1 for e in events if e.type == EventType.POLICY_DECIDED and "REVIEW" in str(e.payload.get("status", "")).upper())
        waiting = sum(1 for e in events if e.type == EventType.APPROVAL_REQUESTED)

        tree = reduce_events_to_tree(events, session_id=session_id)
        meta = sess.get("metadata", {})
        execution_mode = sess.get("execution_mode") or meta.get("execution_mode", "local_restricted")
        ws_root = meta.get("workspace_root") or meta.get("working_directory") or get_default_workspace_root()

        # Obtener configuración efectiva del DecisionRuntime si existe
        dec_effective = None
        with self._lock:
            s_runtime = self._session_runtimes.get(session_id)
            if s_runtime and s_runtime.decision_runtime:
                dec_effective = s_runtime.decision_runtime.config.model_dump()
            elif meta.get("decision_model"):
                d_m = meta.get("decision_model")
                dec_effective = d_m.model_dump() if hasattr(d_m, "model_dump") else d_m

        return {
            "session_id": session_id,
            "goal": sess.get("goal", ""),
            "status": sess.get("status", "Active"),
            "agent_name": sess.get("agent_name", "CodingAgent"),
            "execution_mode": execution_mode,
            "workspace_root": ws_root,
            "operator_approval_status": "Review Required" if waiting > 0 else "Normal",
            "created_at": sess.get("created_at"),
            "updated_at": events[-1].timestamp if events else sess.get("updated_at"),
            "total_decisions": total_decisions,
            "allowed_count": allowed,
            "blocked_count": blocked,
            "review_count": review,
            "waiting_count": waiting,
            "event_count": len(events),
            "node_count": tree.node_count,
            "decision_model_effective": dec_effective,
        }

    def get_session_snapshot(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Snapshot completo con el árbol de decisiones derivado deterministamente."""
        summary = self.get_session_summary(session_id)
        if not summary:
            return None

        events = self.event_bus.get_all_events(session_id)
        tree = reduce_events_to_tree(events, session_id=session_id)

        return {
            "session_id": session_id,
            "goal": summary.get("goal", ""),
            "status": summary.get("status", "Active"),
            "agent_name": summary.get("agent_name", "CodingAgent"),
            "execution_mode": summary.get("execution_mode", "local_restricted"),
            "created_at": summary.get("created_at"),
            "tree": tree.model_dump(mode="json"),
            "summary": summary,
        }

    def delete_session(self, session_id: str) -> bool:
        """Elimina una sesión y purga todos sus estados, eventos, memoria y checkpoints."""
        with self._lock:
            # 1. Detener worker si está en ejecución
            if session_id in self._running_missions:
                self._running_missions[session_id]["stopped"] = True
                self._running_missions.pop(session_id, None)

            # 2. Eliminar de metadatos en memoria
            existed_in_meta = session_id in self._sessions_meta
            self._sessions_meta.pop(session_id, None)

        # 3. Eliminar de base de datos de estado / checkpoints
        existed_in_state = self.state_store.delete_session(session_id)

        # 4. Eliminar eventos asociados del EventBus
        self.event_bus.delete_session(session_id)

        return existed_in_meta or existed_in_state

    def clear_old_sessions(
        self,
        only_completed: bool = True,
        exclude_session_id: Optional[str] = None,
    ) -> int:
        """Purga sesiones antiguas (por defecto solo completadas) para liberar espacio."""
        all_sessions = self.list_sessions()
        deleted_count = 0

        for sess in all_sessions:
            sid = sess.get("session_id")
            if not sid or sid == exclude_session_id:
                continue

            status_val = (sess.get("status") or "").lower()
            should_delete = False
            if only_completed:
                if status_val in ("completed", "stopped", "finished", "finalizada"):
                    should_delete = True
            else:
                should_delete = True

            if should_delete:
                if self.delete_session(sid):
                    deleted_count += 1

        return deleted_count



    def get_available_providers(self) -> Dict[str, Any]:
        """Obtiene dinámicamente el catálogo de providers disponibles (delegado en DiagnosticsCoordinator)."""
        return self.diagnostics_coordinator.get_available_providers()

    def _get_available_providers_impl(self) -> Dict[str, Any]:
        """Implementación interna del catálogo dinámico de providers."""
        from praxeon.providers.registry import default_registry

        # Calcular telemetría observada agregada por provider
        latency_stats: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            for _sid, sess_rt in getattr(self, "_session_runtimes", {}).items():
                if hasattr(sess_rt, "decision_runtime"):
                    tel = sess_rt.decision_runtime.get_telemetry()
                    pid = tel.get("provider_id")
                    avg = tel.get("average_latency_ms")
                    cnt = tel.get("evaluations_count", 0)
                    if pid and avg is not None and cnt > 0:
                        if pid not in latency_stats:
                            latency_stats[pid] = {"total_ms": 0.0, "count": 0}
                        latency_stats[pid]["total_ms"] += float(avg) * int(cnt)
                        latency_stats[pid]["count"] += int(cnt)

        raw_providers = default_registry.list_providers()
        decision_providers = []
        for p in raw_providers:
            pid = p["provider_id"]
            stats = latency_stats.get(pid)
            if stats and stats["count"] > 0:
                avg_ms = round(stats["total_ms"] / stats["count"], 2)
                avg_disp = f"{avg_ms} ms"
            else:
                avg_ms = None
                avg_disp = "N/A"

            meta = {
                "laya": {
                    "name": "LAYA System-1",
                    "model_id": "laya-v1",
                    "version": "0.3.0",
                    "supported_backends": ["local", "api"],
                    "fallbacks": ["mock", "replay"],
                },
                "typesafe": {
                    "name": "TypeSafe AI (Legacy)",
                    "model_id": "typesafe-v1",
                    "version": "0.7.0",
                    "supported_backends": ["api"],
                    "fallbacks": ["mock", "replay"],
                },
                "replay": {
                    "name": "Replay Provider",
                    "model_id": "replay-v1",
                    "version": "1.0.0",
                    "supported_backends": ["local"],
                    "fallbacks": ["mock"],
                },
                "mock": {
                    "name": "Mock Provider",
                    "model_id": "mock-v1",
                    "version": "1.0.0",
                    "supported_backends": ["local"],
                    "fallbacks": [],
                },
            }.get(pid, {
                "name": pid.title(),
                "model_id": f"{pid}-v1",
                "version": "1.0.0",
                "supported_backends": ["local"],
                "fallbacks": ["mock"],
            })

            decision_providers.append({
                "provider_id": pid,
                "name": meta["name"],
                "model_id": meta["model_id"],
                "version": meta["version"],
                "backend": p.get("default_backend") or "local",
                "supported_backends": meta["supported_backends"],
                "description": p.get("description", ""),
                "available": bool(p.get("installed", False)),
                "installed": bool(p.get("installed", False)),
                "required_extra": p.get("required_extra"),
                "average_latency_ms": avg_ms,
                "average_latency_display": avg_disp,
                "fallbacks": meta["fallbacks"],
                "category": "decision_provider",
            })

        llm_providers = [
            {"provider_id": "simulator", "name": "Simulated Agent (Offline)", "default_model": "simulator-agent", "type": "offline"},
            {"provider_id": "groq", "name": "Groq Cloud", "default_model": "llama-3.3-70b-versatile", "type": "cloud"},
            {"provider_id": "ollama", "name": "Ollama Local", "default_model": "qwen2.5-coder:7b", "type": "local"},
            {"provider_id": "gemini", "name": "Google Gemini", "default_model": "gemini-1.5-flash", "type": "cloud"},
            {"provider_id": "openai", "name": "OpenAI", "default_model": "gpt-4o-mini", "type": "cloud"},
            {"provider_id": "openrouter", "name": "OpenRouter", "default_model": "anthropic/claude-3.5-sonnet", "type": "cloud"},
        ]

        return {
            "decision_providers": decision_providers,
            "llm_providers": llm_providers,
        }



# generate_goal_tailored_steps ha sido modularizado formalmente en:
# praxeon.server.services.mission_service y se re-exporta para preservar compatibilidad retroactiva.
__all__ = [
    'RuntimeApplicationService',
    'generate_goal_tailored_steps',
    'get_runtime_service',
    'set_runtime_service',
    'get_agent_service',
    'set_active_security_profile',
    'get_active_security_profile',
    'is_auth_required',
    'verify_api_key',
]



# Singleton de servicio para la aplicación Web
_runtime_service_instance: Optional[RuntimeApplicationService] = None
_service_lock = threading.Lock()


def get_runtime_service() -> RuntimeApplicationService:
    """Devuelve la instancia singleton del servicio de aplicación."""
    global _runtime_service_instance
    with _service_lock:
        if _runtime_service_instance is None:
            _runtime_service_instance = RuntimeApplicationService()
        return _runtime_service_instance


def set_runtime_service(service: Optional[RuntimeApplicationService]) -> None:
    """Inyecta una instancia de servicio (útil para tests con bases de datos aisladas)."""
    global _runtime_service_instance
    with _service_lock:
        _runtime_service_instance = service


def get_agent_service() -> AgentService:
    """Devuelve el servicio de agentes del runtime."""
    return get_runtime_service().get_agent_service()


_ACTIVE_SECURITY_PROFILE: Optional[str] = None


def set_active_security_profile(profile: Optional[str]) -> None:
    """Establece el perfil de seguridad activo del runtime."""
    global _ACTIVE_SECURITY_PROFILE, _warned_dev_auth
    _ACTIVE_SECURITY_PROFILE = profile
    _warned_dev_auth = False


def get_active_security_profile() -> Optional[str]:
    """Retorna el perfil de seguridad activo si fue establecido."""
    return _ACTIVE_SECURITY_PROFILE


_warned_dev_auth = False


def is_auth_required(profile: Optional[str] = None, client_host: Optional[str] = None) -> bool:
    """Determina si la autenticación por API key es obligatoria.
    
    Es obligatoria si:
    1. PRAXEON_PROFILE == 'production' o PRAXEON_ENV == 'production' (o perfil activo == 'production').
    2. PRAXEON_REQUIRE_AUTH == '1' / 'true'.
    3. PRAXEON_API_KEY está configurada explícitamente en el entorno.
    4. La petición proviene de una interfaz de red externa (no localhost/loopback).
    """
    if client_host and client_host.lower() not in ("127.0.0.1", "localhost", "::1", "testclient"):
        return True
    prof = (profile or _ACTIVE_SECURITY_PROFILE or os.environ.get("PRAXEON_PROFILE") or os.environ.get("PRAXEON_ENV") or "dev").lower().strip()
    if prof == "production":
        return True
    if os.environ.get("PRAXEON_REQUIRE_AUTH", "").strip().lower() in ("1", "true", "yes"):
        return True
    if bool(os.environ.get("PRAXEON_API_KEY", "").strip()):
        return True
    return False


from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from praxeon.server.services.auth_service import (
    Permission,
    Role,
    ROLE_PERMISSIONS,
    TenantContext,
    constant_time_equals,
)


def get_auth_service(runtime: RuntimeApplicationService = Depends(get_runtime_service)) -> AuthService:
    """Dependency para inyectar la instancia de AuthService del runtime."""
    return runtime.get_auth_service()


def get_current_tenant(
    request: Request = None,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    praxeon_access_token: Optional[str] = Cookie(None, alias=AuthService.COOKIE_ACCESS_NAME),
    auth_service: AuthService = Depends(get_auth_service),
) -> TenantContext:
    """Dependency que autentica e inyecta el TenantContext con RBAC desde JWT, Cookie o API Key."""
    app_profile = getattr(request.app.state, "security_profile", None) if (request and hasattr(request, "app") and hasattr(request.app, "state")) else None
    client_host = request.client.host if (request and request.client) else None
    required = is_auth_required(profile=app_profile, client_host=client_host)

    cookie_val = praxeon_access_token if isinstance(praxeon_access_token, str) else None
    api_key_val = x_api_key if isinstance(x_api_key, str) else None
    auth_val = authorization if isinstance(authorization, str) else None
    service = auth_service if isinstance(auth_service, AuthService) else get_runtime_service().get_auth_service()

    # 1. Intentar token desde cookie HttpOnly o API Key
    token = cookie_val or api_key_val

    # 2. Intentar token desde cabeceras
    if not token and auth_val:
        if auth_val.startswith("Bearer "):
            token = auth_val[7:].strip()
        else:
            token = auth_val.strip()

    if not required and not token:
        # Modo dev sin autenticación obligatoria
        global _warned_dev_auth
        if not _warned_dev_auth:
            logger.warning("WARNING: PRAXEON running without API key authentication. Do not use in production.")
            _warned_dev_auth = True
        return TenantContext(
            tenant_id="dev_tenant",
            user_id="dev_user",
            role=Role.ADMIN,
            permissions=ROLE_PERMISSIONS[Role.ADMIN],
        )

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida: proporcione cookie 'praxeon_access_token', 'Authorization: Bearer <token>' o 'X-API-Key'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Si parece un JWT (3 segmentos)
    if token.count(".") == 2:
        try:
            return service.authenticate_jwt(token)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Token JWT inválido o expirado: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from e

    # Si es una API Key estática
    expected_key = os.environ.get("PRAXEON_API_KEY") or os.environ.get("PRAXEON_SECRET_KEY")
    if expected_key and constant_time_equals(token, expected_key):
        return TenantContext(
            tenant_id="default_tenant",
            user_id="admin_apikey",
            role=Role.ADMIN,
            permissions=ROLE_PERMISSIONS[Role.ADMIN],
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas: token o API key no reconocidos.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_permission(permission: Permission):
    """Dependency factory para enforcing declarativo de permisos RBAC en rutas FastAPI."""
    def _perm_checker(tenant: TenantContext = Depends(get_current_tenant)) -> TenantContext:
        if not tenant.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permiso insuficiente: se requiere '{permission.value}'.",
            )
        return tenant
    return _perm_checker


def require_route_permission(read: Permission, write: Permission):
    """Dependency para routers: exige `read` en GET/HEAD/OPTIONS y `write` en métodos mutantes."""
    def _checker(
        request: Request,
        tenant: TenantContext = Depends(get_current_tenant),
    ) -> TenantContext:
        needed = read if request.method in ("GET", "HEAD", "OPTIONS") else write
        if not tenant.has_permission(needed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permiso insuficiente: se requiere '{needed.value}'.",
            )
        return tenant
    return _checker


def verify_api_key(
    request: Request = None,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    praxeon_access_token: Optional[str] = Cookie(None, alias=AuthService.COOKIE_ACCESS_NAME),
    auth_service: AuthService = Depends(get_auth_service),
) -> Optional[str]:
    """Dependency de FastAPI para validar la presencia y autenticidad de API Key o JWT (compatibilidad retroactiva)."""
    global _warned_dev_auth

    app_profile = getattr(request.app.state, "security_profile", None) if (request and hasattr(request, "app") and hasattr(request.app, "state")) else None
    client_host = request.client.host if (request and request.client) else None
    required = is_auth_required(profile=app_profile, client_host=client_host)
    expected_key = os.environ.get("PRAXEON_API_KEY") or os.environ.get("PRAXEON_SECRET_KEY")

    cookie_val = praxeon_access_token if isinstance(praxeon_access_token, str) else None
    api_key_val = x_api_key if isinstance(x_api_key, str) else None
    auth_val = authorization if isinstance(authorization, str) else None
    service = auth_service if isinstance(auth_service, AuthService) else get_runtime_service().get_auth_service()

    # Extraer token de cookie o cabeceras
    token = cookie_val or api_key_val
    if not token and auth_val:
        if auth_val.startswith("Bearer "):
            token = auth_val[7:].strip()
        else:
            token = auth_val.strip()

    if not required and not token:
        if not _warned_dev_auth:
            logger.warning("WARNING: PRAXEON running without API key authentication. Do not use in production.")
            _warned_dev_auth = True
        return None

    if not token:
        if not expected_key:
            logger.error("Fallo de seguridad evitado: Autenticación requerida pero no hay PRAXEON_API_KEY configurada.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error de configuración del servidor: Autenticación requerida pero no se ha establecido PRAXEON_API_KEY.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida: proporcione 'X-API-Key' o 'Authorization: Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 1. Si es un JWT válido
    if token.count(".") == 2:
        try:
            service.authenticate_jwt(token)
            return token
        except ValueError:
            pass

    # 2. Si es una API Key estática
    if expected_key and constant_time_equals(token, expected_key):
        return token

    # Si no había expected_key pero tampoco es JWT válido
    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error de configuración del servidor: Autenticación requerida pero no se ha establecido PRAXEON_API_KEY.",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas: API key o token no coincide con el servidor.",
        headers={"WWW-Authenticate": "Bearer"},
    )



