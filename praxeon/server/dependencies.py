"""Capa de servicios de aplicación y dependencias para el Web Server (praxeon/server/dependencies.py).

Especificación PRAXEON 1.0 (Sección 2.1 y Sección 8).
La API Web nunca ejecuta comandos directamente ni contiene reglas de seguridad
paralelas. Toda operación pasa por el pipeline canónico del runtime:
Proposal -> Evidence -> Risk -> Provider -> Policy -> Capability -> Execution
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib


import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

logger = logging.getLogger("praxeon.server.dependencies")

from praxeon.config import PraxeonConfig, default_config, resolve_cache_dir
from praxeon.core.state_graph import StateGraph
from praxeon.domain.action import compute_action_hash
from praxeon.domain.decision import (
    CapabilityPayload,
    DecisionReceipt,
    DecisionStatus,
    ExecutionMode,
    PolicyDecision,
    compute_receipt_signature,
    compute_state_hash,
    sign_receipt,
    verify_capability_signature,
    verify_receipt_signature,
)
from praxeon.domain.events import EventType, RuntimeEvent
from praxeon.domain.models import (
    ActionCandidate,
    Goal,
    ProviderAssessment,
    RiskAssessment,
    RiskLevel,
    ToolCall,
)
from praxeon.domain.tree import DecisionTree, NodeKind, NodeStatus, TreeNode
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
from praxeon.runtime.executor import PolicyViolation, SecureExecutor, ToolObservation
from praxeon.runtime.nonce_store import NonceStore, SqliteNonceStore
from praxeon.context.manager import ContextManager
from praxeon.agents.bus import AgentMessageBus
from praxeon.agents.registry import AgentRegistry
from praxeon.agents.templates import AgentTemplateCatalog
from praxeon.routing.router import AgentRouter
from praxeon.workflows.editor_service import WorkflowEditorService
from praxeon.runtime.adaptive.runtime import AdaptiveAgentRuntime
from praxeon.runtime.sandbox import (
    LocalProcessSandbox,
    SandboxExecutionResult,
    SandboxTier,
    get_default_workspace_root,
)
from praxeon.runtime.state import SessionState, StepRecord
from praxeon.runtime.state_store import SqliteStateStore
from praxeon.runtime.tree_reducer import TreeReducer, reduce_events_to_tree
from praxeon.server.schemas.action import ProposeActionRequest
from praxeon.server.schemas.decision import (
    ConfirmDecisionResponse,
    DecisionDetailResponse,
    DecisionResponse,
    ExecuteDecisionResponse,
    PolicyDTO,
    ProviderEvaluationDTO,
    RiskDTO,
)
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


class RuntimeApplicationService:
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

        # Configurar proveedor supervisor semántico
        if provider is not None:
            self.provider = provider
        elif self.config.provider.name.lower() in ("mock", "mockprovider", "test", "testing"):
            self.provider = MockProvider()
        elif self.config.provider.name.lower() in ("replay", "replayprovider"):
            self.provider = ReplayProvider()
        elif self.config.provider.name.lower() == "laya":
            self.provider = LayaProvider(backend=self.config.provider.laya_backend)
        else:
            self.provider = TypeSafeAdapter(
                api_key=self.config.provider.api_key,
                model_name=self.config.provider.model,
            )

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

    # =========================================================================
    # PIPELINE DE DECISIÓN FORMAL (Proposal -> Evidence -> Risk -> Policy -> Capability)
    # =========================================================================

    def propose_action(
        self,
        session_id: str,
        proposal: ProposeActionRequest,
    ) -> DecisionResponse:
        """Punto de entrada de una propuesta externa pasando por todo el pipeline formal."""
        state = self.state_store.load_state(session_id)
        if not state:
            # Crear sesión sobre la marcha si no existiese
            goal_text = proposal.context.get("goal") or "Supervised Agent Task"
            self.create_session(goal=goal_text, session_id=session_id)
            state = self.state_store.load_state(session_id)

        action_id = proposal.action_id or f"act_{len(state.steps) + 1}"
        decision_id = f"d_{uuid.uuid4().hex[:6]}"
        now = datetime.now(timezone.utc)

        # Procedencia y gobernanza
        prov_dict = proposal.provenance if isinstance(proposal.provenance, dict) else {}
        is_synth = bool(proposal.synthetic_fallback or prov_dict.get("synthetic_fallback", False))
        m_source = proposal.model_source or prov_dict.get("model_source") or prov_dict.get("source", "ExternalAgent")

        action = ActionCandidate(
            id=action_id,
            description=proposal.thought_rationale or f"{proposal.tool} {proposal.operation or ''}".strip(),
            tool_call=ToolCall(tool_name=proposal.tool, arguments=proposal.arguments),
            synthetic_fallback=is_synth,
            model_source=m_source,
        )

        session_mode = "local_restricted"
        with self._lock:
            if session_id in self._sessions_meta:
                session_mode = self._sessions_meta[session_id].get("execution_mode") or session_mode
        if not session_mode or session_mode == "local_restricted":
            if state and state.metadata:
                session_mode = state.metadata.get("execution_mode", session_mode)

        # 1. Emitir evento: action.proposed con parent_id respetado para bifurcaciones y retrocesos
        parent_id = proposal.parent_id or (f"act_{len(state.steps)}" if state.steps else f"root_{session_id}")
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.ACTION_PROPOSED,
            node_id=action_id,
            parent_id=parent_id,
            decision_id=decision_id,
            payload={
                "action_id": action_id,
                "parent_id": parent_id,
                "tool": proposal.tool,
                "operation": proposal.operation or "",
                "arguments": proposal.arguments,
                "source": prov_dict.get("source", "ExternalAgent"),
                "step": prov_dict.get("step", 1),
                "thought_rationale": proposal.thought_rationale,
                "synthetic_fallback": is_synth,
                "model_source": m_source,
            },
        )

        # 1.5. Clasificación contextual de la operación concreta (Sección 4)
        # SEGURIDAD AUDITORÍA (Finding 13): El comando ejecutable real contenido en arguments
        # DEBE prevalecer sobre el campo 'operation' (que es un label decorativo suministrado por el
        # llamante y susceptible a spoofing/suplantación para evadir la política de seguridad).
        cmd_from_args = ""
        if proposal.arguments:
            cmd_from_args = str(
                proposal.arguments.get("command")
                or proposal.arguments.get("cmd")
                or proposal.arguments.get("raw")
                or ""
            ).strip()

        if proposal.tool in ("run_command", "run_script") and cmd_from_args:
            op_string = cmd_from_args
        elif cmd_from_args:
            op_string = f"{proposal.tool} {cmd_from_args}".strip() if proposal.tool else cmd_from_args
        else:
            op_string = (proposal.operation or "").strip()
            if not op_string:
                op_string = proposal.tool or ""
            elif proposal.tool and proposal.tool != "run_command" and not op_string.startswith(proposal.tool):
                op_string = f"{proposal.tool} {op_string}".strip()

        op_assessment = self.command_classifier.classify(
            op_string,
            tool=proposal.tool,
            arguments=proposal.arguments,
            context={"goal": state.goal.objective, "session_id": session_id},
        )
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.OPERATION_CLASSIFIED,
            node_id=action_id,
            decision_id=decision_id,
            payload=op_assessment.model_dump(),
        )

        # 2. Evaluación de Evidencia Empírica y Existencia de Recursos en Disco
        is_missing_resource = False
        missing_resource_name = ""
        target_path = str(proposal.arguments.get("path") or proposal.arguments.get("file") or "").strip()
        if proposal.tool in ("read_file", "view_file") and target_path:
            norm_target = os.path.expanduser(os.path.expandvars(target_path))
            full_target = norm_target if os.path.isabs(norm_target) else os.path.join(os.getcwd(), norm_target)
            if not os.path.exists(full_target):
                is_missing_resource = True
                missing_resource_name = target_path
        elif proposal.tool in ("run_command", "run_script"):
            cmd_raw = str(proposal.arguments.get("command") or proposal.arguments.get("cmd") or "").strip()
            for runner in ("python ", "python3 ", "node ", "bash ", "sh "):
                if cmd_raw.startswith(runner):
                    script_part = cmd_raw[len(runner):].strip().split()[0].strip('"\'')
                    if script_part.endswith((".py", ".js", ".sh", ".ts")):
                        full_script = script_part if os.path.isabs(script_part) else os.path.join(os.getcwd(), script_part)
                        if not os.path.exists(full_script):
                            is_missing_resource = True
                            missing_resource_name = script_part
                    break

        # Detección de conclusión prematura / evasiva (finish con texto evasivo o vacío)
        is_premature_finish = False
        if proposal.tool in ("finish", "complete_task", "done") and state:
            goal_lower = state.goal.objective.lower()
            summary_text = str(proposal.arguments.get("summary") or proposal.arguments.get("final_answer") or "").strip()

            # Tareas generativas, creativas o de especificación (historias, requisitos, diseño, nuevo proyecto, etc.)
            is_generative_or_spec = any(w in goal_lower for w in (
                "user stories", "historias de usuario", "requisito", "requirements", "diseño",
                "propuesta", "nuevo proyecto", "nueva app", "arquitectura", "especific", "cuento",
                "historia", "poema", "estructura de carpetas", "opin", "calific", "informe formal"
            ))

            # Es investigación de repositorio si no es una tarea generativa/especificación y pide informe/análisis de proyecto o código
            is_local_codebase_investigation = (
                not is_generative_or_spec
                and any(w in goal_lower for w in (
                    "informe del proyecto", "informe", "report", "audita", "inspecciona", "analiza",
                    "cómo funciona", "investiga", "describe el proyecto", "código"
                ))
            )

            # Evasión real: texto vacío, extremadamente corto (< 30 chars) o con frases de evasión explícitas
            is_evasive = len(summary_text) < 30 or any(m in summary_text.lower() for m in ("pendiente de lectura", "sin analizar", "no se ha realizado", "no he podido revisar"))

            successful_obs = [s for s in state.steps if s.observation and len(str(s.observation).strip()) > 20]
            if is_evasive or (is_local_codebase_investigation and len(successful_obs) == 0 and len(summary_text) < 250):
                is_premature_finish = True

        evidences = self.evidence_engine.assess(state, action)
        if is_missing_resource or is_premature_finish:
            grounding_score = 0.05
        else:
            grounding_score = 0.85 if evidences else 0.40

        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.EVIDENCE_EVALUATED,
            node_id=action_id,
            decision_id=decision_id,
            payload={
                "evidence_count": len(evidences),
                "grounding_score": grounding_score,
                "claims_evaluated": [e.claim.statement for e in evidences],
                "resource_missing": is_missing_resource,
                "missing_resource": missing_resource_name if is_missing_resource else None,
                "premature_finish": is_premature_finish,
            },
        )

        # 3. Evaluación de Riesgo Operacional (sensible al modo de ejecución y reconciliación semántica)
        raw_risk_assessment = self.risk_engine.assess_action_risk(action, execution_mode=session_mode)
        risk_assessment = self.policy_engine.reconciler.reconcile(
            base_risk=raw_risk_assessment,
            operation_assessment=op_assessment,
            tool_name=proposal.tool,
            tool_spec=self.registry.get_tool(proposal.tool),
            action=action,
            available_evidence=state.evidence,
            execution_mode=session_mode,
            forbidden_tools=state.forbidden_tools,
        )
        risk_level_str = (
            risk_assessment.level.value
            if hasattr(risk_assessment.level, "value")
            else str(risk_assessment.level)
        ).upper()
        level_score_map = {"LOW": 0.15, "MEDIUM": 0.45, "HIGH": 0.80, "CRITICAL": 0.95}
        risk_score = level_score_map.get(risk_level_str, 0.20)
        risk_dto = RiskDTO(
            level=risk_level_str,
            score=risk_score,
            reasons=risk_assessment.reasons,
        )
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.RISK_ASSESSED,
            node_id=action_id,
            decision_id=decision_id,
            payload=risk_dto.model_dump(),
        )

        # 4. Evaluación de Proveedores Semánticos (LAYA / TypeSafe)
        assessments = self.provider.evaluate(state, [action])
        assessment = assessments[0] if assessments else ProviderAssessment(
            provider=self.config.provider.name,
            available=False,
            confidence=0.0,
            loop_probability=None,
            grounded_probability=None,
            progress_probability=None,
            reason_codes=["PROVIDER_NO_EVALUATION"],
        )
        if is_missing_resource or is_premature_finish:
            current_reasons = list(assessment.reason_codes or [])
            if is_missing_resource and f"UNGROUNDED_FILE_NOT_FOUND ({missing_resource_name})" not in current_reasons:
                current_reasons.append(f"UNGROUNDED_FILE_NOT_FOUND ({missing_resource_name})")
            if is_premature_finish and "PREMATURE_COMPLETION_WITHOUT_EVIDENCE" not in current_reasons:
                current_reasons.append("PREMATURE_COMPLETION_WITHOUT_EVIDENCE")
            assessment = assessment.model_copy(
                update={
                    "grounded_probability": min(assessment.grounded_probability or 1.0, 0.05),
                    "reason_codes": current_reasons,
                }
            )

        prov_avail = bool(getattr(assessment, "available", True))
        if not prov_avail:
            score_val = None
        else:
            raw_prob = getattr(assessment, "progress_probability", None)
            if raw_prob is None:
                raw_prob = getattr(assessment, "confidence", None)
            score_val = (
                round(float(raw_prob), 2)
                if (raw_prob is not None and isinstance(raw_prob, (int, float)))
                else None
            )

        loop_prob = getattr(assessment, "loop_probability", None)
        if not prov_avail:
            verdict_val = "UNAVAILABLE"
        elif loop_prob is not None and loop_prob >= 0.5:
            verdict_val = "REVIEW"
        else:
            verdict_val = "ALLOW"

        prov_name = getattr(self.provider, "name", "").lower()
        if prov_name in ("mock", "replay"):
            provider_name_display = prov_name
        elif self.config.provider.name.lower() == "laya" or prov_name == "laya":
            provider_name_display = "LAYA"
        else:
            provider_name_display = "TypeSafe"

        provider_dtos = [
            ProviderEvaluationDTO(
                name=provider_name_display,
                score=score_val,
                verdict=verdict_val,
                available=prov_avail,
            )
        ]
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.PROVIDER_EVALUATED,
            node_id=action_id,
            decision_id=decision_id,
            payload={
                "provider_name": provider_dtos[0].name,
                "score": provider_dtos[0].score,
                "verdict": provider_dtos[0].verdict,
                "available": provider_dtos[0].available,
            },
        )

        # 5. Decisión Operacional de Política (PolicyEngine)
        decision, receipt = self.policy_engine.evaluate_action(
            action=action,
            state=state.to_snapshot(),
            provider_assessment=assessment,
            available_evidence=state.evidence,
            forbidden_tools=state.forbidden_tools,
            risk_assessment=risk_assessment,
            operation_assessment=op_assessment,
            session_id=session_id,
            execution_mode=ExecutionMode(session_mode),
        )

        # Obtener configuración de autonomía de la sesión
        session_meta = {}
        with self._lock:
            if session_id in self._sessions_meta:
                session_meta = self._sessions_meta[session_id].get("metadata") or {}
        if not session_meta and state and state.metadata:
            session_meta = state.metadata

        is_autonomous = bool(
            session_meta.get("allow_unattended_execution")
            or session_meta.get("autonomous")
            or False
        )

        # Mapear estado con precedencia determinista (BLOCK > REPLAN > REVIEW > ALLOW)
        requires_conf = decision.requires_confirmation
        mode_val = str(session_mode.value if hasattr(session_mode, "value") else session_mode or "").lower()
        full_access_authorized = bool(
            session_meta.get("full_access_authorized_by_operator")
            or session_meta.get("operator_authorized")
        )

        if decision.status == DecisionStatus.BLOCK or decision.risk.level == RiskLevel.CRITICAL or risk_assessment.level == RiskLevel.CRITICAL:
            # Veto incondicional: BLOCK nunca ejecuta, ni en Full Access ni en modo autónomo
            status_str = "BLOCK"
            policy_decision_str = "BLOCK"
            requires_conf = False
        elif decision.status == DecisionStatus.REPLAN:
            status_str = "REPLAN"
            policy_decision_str = "REPLAN"
            requires_conf = False
        elif requires_conf:
            is_unregistered = bool(action.tool_call and not self.registry.is_known(action.tool_call.tool_name))
            if mode_val == "full_access" and is_autonomous and full_access_authorized and not is_unregistered:
                # FULL_ACCESS + AUTONOMOUS: Requiere flag explícito con advertencia auditada Y autorización previa verificada del operador
                requires_conf = False
                status_str = "ALLOW"
                policy_decision_str = "ALLOW"
                logger.warning(
                    "[SECURITY AUDIT] Sesión '%s' ejecutando en modo FULL_ACCESS_AUTONOMOUS: "
                    "Confirmación humana omitida por autorización explícita previa y verificada del operador para acción '%s'.",
                    session_id,
                    action.tool_call.tool_name,
                )
            else:
                # FULL_ACCESS estándar u otros modos / autónomo no autorizado / herramienta no registrada: retiene obligatoriamente la confirmación interactiva
                status_str = "REVIEW"
                policy_decision_str = "REQUIRE_HUMAN_CONFIRMATION"

        else:
            status_str = decision.status.value.upper()
            policy_decision_str = status_str

        policy_dto = PolicyDTO(
            decision=policy_decision_str,
            reason_codes=decision.reason_codes,
            requires_confirmation=requires_conf,
        )

        policy_node_id = f"{action_id}_policy"
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.POLICY_DECIDED,
            node_id=policy_node_id,
            parent_id=action_id,
            decision_id=decision_id,
            payload={
                "status": status_str,
                "reason_code": decision.reason_codes[0] if decision.reason_codes else "POLICY_EVALUATED",
                "requires_confirmation": requires_conf,
                "execution_mode": session_mode,
            },
        )

        # 6. Emisión de Capability (Solo si ALLOW sin revisión humana obligatoria)
        capability_data: Optional[Dict[str, Any]] = None
        expires_at: Optional[datetime] = None

        if status_str == "ALLOW":
            expires_at = now + timedelta(minutes=5)
            # Firmar recibo con su execution_mode canónico
            receipt_with_exp = receipt.model_copy(
                update={
                    "decision_id": decision_id,
                    "decision_status": DecisionStatus.ALLOW,
                    "expires_at": expires_at,
                    "execution_mode": ExecutionMode(session_mode),
                }
            )
            signed = sign_receipt(receipt_with_exp, self.policy_engine.secret_key)
            cap = signed.to_capability_payload(allowed_tools=[proposal.tool])
            capability_data = cap.model_dump(mode="json") if cap else None

            self.event_bus.emit(
                session_id=session_id,
                event_type=EventType.CAPABILITY_ISSUED,
                node_id=action_id,
                decision_id=decision_id,
                payload=capability_data or {},
            )
            receipt = signed
        elif status_str == "REVIEW":
            self.event_bus.emit(
                session_id=session_id,
                event_type=EventType.APPROVAL_REQUESTED,
                node_id=action_id,
                decision_id=decision_id,
                payload={
                    "risk_level": risk_dto.level,
                    "reasons": risk_dto.reasons,
                    "reason_code": policy_dto.reason_codes,
                    "execution_mode": session_mode,
                },
            )
        elif status_str == "BLOCK":
            self.event_bus.emit(
                session_id=session_id,
                event_type=EventType.DECISION_PRUNED,
                node_id=action_id,
                decision_id=decision_id,
                payload={"reason": ", ".join(decision.reason_codes)},
            )

        # Guardar en registro durable de decisiones (SQLite WAL + memoria)
        decision_record = {
            "decision_id": decision_id,
            "session_id": session_id,
            "action_id": action_id,
            "action": action,
            "status": status_str,
            "execution_mode": session_mode,
            "action_hash": receipt.action_hash,
            "risk": risk_dto,
            "providers": provider_dtos,
            "policy": policy_dto,
            "capability": capability_data,
            "expires_at": expires_at,
            "receipt": receipt,
            "evidence": evidences,
            "grounding_score": grounding_score,
            "operation_assessment": op_assessment.model_dump(),
            "operation_category": op_assessment.category.value if hasattr(op_assessment.category, "value") else str(op_assessment.category),
            "created_at": now,
        }
        self.decision_repository.save(decision_record)
        with self._lock:
            self._decisions[decision_id] = decision_record

        # Registrar decisiones terminales (REPLAN / BLOCK) en el estado de la sesión
        # (Las decisiones ALLOW se registran en execute_decision tras verificar el hash criptográfico del estado)
        if status_str in ("REPLAN", "BLOCK"):
            mapped_status = DecisionStatus.BLOCK if status_str == "BLOCK" else DecisionStatus.REPLAN
            state.add_step(
                action=action,
                decision=PolicyDecision(
                    status=mapped_status,
                    reason_codes=decision.reason_codes,
                ),
                observation=None,
            )
            self.state_store.save_state(state)

        return DecisionResponse(
            decision_id=decision_id,
            session_id=session_id,
            status=status_str,
            action_hash=receipt.action_hash,
            risk=risk_dto,
            providers=provider_dtos,
            policy=policy_dto,
            execution_mode=session_mode,
            capability=capability_data,
            expires_at=expires_at,
            operation_assessment=op_assessment.model_dump(),
        )

    # =========================================================================
    # DECISION INSPECTOR (4 Tabs: Decision, Evidence, Policy, Receipt)
    # =========================================================================

    def get_decision_detail(self, decision_id: str) -> Optional[DecisionDetailResponse]:
        """Recupera los datos estructurados en las 4 pestañas requeridas por la Sección 6.3."""
        with self._lock:
            record = self._decisions.get(decision_id)

        # Supervivencia a reinicios: recuperar de SQLite WAL
        if not record:
            record = self.decision_repository.get(decision_id)
            if not record:
                # Deterministic fallback: reconstruir desde EventStore
                try:
                    conn = self.event_bus.store._get_connection()
                    cur = conn.cursor()
                    cur.execute("SELECT session_id FROM runtime_events WHERE decision_id = ? LIMIT 1", (decision_id,))
                    row = cur.fetchone()
                    if row:
                        found_sid = row[0]
                        evs = self.event_bus.get_all_events(found_sid)
                        reconstructed = self.decision_repository.reconstruct_from_events(found_sid, evs)
                        if decision_id in reconstructed:
                            record = reconstructed[decision_id]
                            self.decision_repository.save(record)
                except Exception as ex:
                    logger.debug("Reconstrucción desde eventos falló para %s: %s", decision_id, ex)

            if record:
                with self._lock:
                    self._decisions[decision_id] = record

        if not record:
            return None

        action: ActionCandidate = record["action"]
        receipt: DecisionReceipt = record["receipt"]
        risk: RiskDTO = record["risk"]
        policy: PolicyDTO = record["policy"]
        providers: List[ProviderEvaluationDTO] = record["providers"]

        # Determinar atributos de ejecución para la subsección Execution
        mode_val = record.get("execution_mode") or getattr(receipt, "execution_mode", "local_restricted")
        if hasattr(mode_val, "value"):
            mode_val = mode_val.value
        state = self.state_store.load_state(record["session_id"])
        working_dir = (state.metadata.get("working_directory") if state and state.metadata else None) or os.getcwd()
        backend_name = (
            "FullAccessExecutor" if mode_val == "full_access"
            else ("DockerContainer" if mode_val == "container" else "LocalProcessSandbox")
        )
        isolation_str = "None (Host OS)" if mode_val == "full_access" else "Active"
        network_str = "Host Direct" if mode_val == "full_access" else "Isolated (Restricted)"

        # 1. Pestaña: Decision
        decision_tab = {
            "decision_id": decision_id,
            "session_id": record["session_id"],
            "action_id": record["action_id"],
            "tool": action.tool_call.tool_name if action.tool_call else None,
            "arguments": action.tool_call.arguments if action.tool_call else {},
            "status": record["status"],
            "provider": providers[0].name if providers else "Unknown",
            "model": self.config.provider.model,
            "risk_level": risk.level,
            "risk_score": risk.score,
            "semantic_score": providers[0].score if providers else 0.0,
            "reason": ", ".join(policy.reason_codes) or "Action assessed against runtime policy.",
            "execution_mode": mode_val,
            "isolation": isolation_str,
            "working_directory": working_dir,
            "network_mode": network_str,
            "execution_backend": backend_name,
            "operation_assessment": record.get("operation_assessment") or (
                receipt.operation_assessment if hasattr(receipt, "operation_assessment") else None
            ),
            "operation_category": record.get("operation_category") or (
                receipt.operation_category if hasattr(receipt, "operation_category") else None
            ),
        }

        # 2. Pestaña: Evidence
        evidence_tab = {
            "evidence_count": len(record.get("evidence", [])),
            "grounding_score": record.get("grounding_score", 0.0),
            "observations_used": [e.source_observation_id for e in record.get("evidence", []) if e.source_observation_id],
            "provenance": getattr(action, "description", ""),
            "freshness": "live",
        }

        # 3. Pestaña: Policy
        policy_tab = {
            "decision": policy.decision,
            "reason_codes": policy.reason_codes,
            "requires_confirmation": policy.requires_confirmation,
            "rules_activated": ["EgressPolicy", "PathContainment", "DoubleVerification"] if risk.level in ("HIGH", "CRITICAL") else ["StandardPolicy"],
            "precedence": "Deterministic Policy Precedence (Safety > Efficiency)",
            "operation_category": record.get("operation_category") or (
                receipt.operation_category if hasattr(receipt, "operation_category") else None
            ),
        }

        # 4. Pestaña: Receipt
        receipt_mode = getattr(receipt.execution_mode, "value", str(receipt.execution_mode)) if hasattr(receipt, "execution_mode") else mode_val
        receipt_tab = {
            "decision_id": receipt.decision_id,
            "session_id": receipt.session_id,
            "action_hash": receipt.action_hash,
            "state_hash": receipt.state_hash,
            "nonce": receipt.nonce,
            "execution_mode": receipt_mode,
            "signature": receipt.signature or "unsigned",
            "has_valid_hmac": bool(receipt.signature and verify_receipt_signature(self.policy_engine.secret_key, receipt)),
            "expires_at": receipt.expires_at.isoformat() if receipt.expires_at else None,
            "is_expired": receipt.is_expired(),
            "is_executed": receipt.is_executed,
            "timestamp": receipt.timestamp.isoformat(),
        }

        return DecisionDetailResponse(
            decision_id=decision_id,
            session_id=record["session_id"],
            action_id=record["action_id"],
            decision_tab=decision_tab,
            evidence_tab=evidence_tab,
            policy_tab=policy_tab,
            receipt_tab=receipt_tab,
        )

    def list_decisions_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        """Lista cronológicamente las decisiones asociadas a una sesión recuperando de SQLite."""
        # 1. Recuperar del repositorio SQLite WAL
        records = self.decision_repository.list_by_session(session_id)
        if not records:
            events = self.event_bus.get_all_events(session_id)
            reconstructed = self.decision_repository.reconstruct_from_events(session_id, events)
            records = list(reconstructed.values())

        # 2. Combinar con caché en memoria
        with self._lock:
            for d in self._decisions.values():
                if d.get("session_id") == session_id:
                    if not any(r.get("decision_id") == d.get("decision_id") for r in records):
                        records.append(d)

        return self._format_decision_records(records)

    def list_all_decisions(self, session_id: Optional[str] = None, limit: int = 150) -> List[Dict[str, Any]]:
        """Lista cronológicamente o por recencia las decisiones del sistema."""
        if session_id:
            return self.list_decisions_for_session(session_id)

        records = self.decision_repository.list_all(limit=limit)
        with self._lock:
            for d in self._decisions.values():
                if not any(r.get("decision_id") == d.get("decision_id") for r in records):
                    records.append(d)

        return self._format_decision_records(records)

    def _format_decision_records(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Formatea registros de decisión para consumo por vistas de auditoría del frontend."""
        results = []
        for d in records:
            act = d.get("action")
            tool_name = None
            args = {}
            description = ""
            if act:
                tool_name = act.tool_call.tool_name if hasattr(act, "tool_call") and act.tool_call else None
                if not tool_name and isinstance(act, dict):
                    tool_name = act.get("tool_call", {}).get("tool_name")
                if hasattr(act, "tool_call") and act.tool_call and hasattr(act.tool_call, "arguments"):
                    args = act.tool_call.arguments or {}
                elif isinstance(act, dict):
                    args = act.get("tool_call", {}).get("arguments", {})
                description = getattr(act, "description", "") or (act.get("description", "") if isinstance(act, dict) else "")

            # Formatear un comando o acción legible
            command = ""
            if tool_name == "run_command":
                command = args.get("command") or args.get("cmd") or description
            elif tool_name in ("read_file", "write_file", "delete_file", "patch_file"):
                target_p = args.get("path") or args.get("file_path") or ""
                command = f"{tool_name}: {target_p}" if target_p else (description or tool_name)
            elif tool_name == "search_web":
                query_p = args.get("query") or args.get("q") or ""
                command = f"search: {query_p}" if query_p else (description or "search_web")
            else:
                if description:
                    command = description
                elif tool_name:
                    sample_args = ", ".join(f"{k}={v}" for k, v in list(args.items())[:2])
                    command = f"{tool_name}({sample_args})"
                else:
                    command = d.get("action_id", "")

            receipt = d.get("receipt")
            receipt_sig = getattr(receipt, "signature", None) if receipt else None
            action_hash = getattr(receipt, "action_hash", None) if receipt else None
            if not receipt_sig and isinstance(receipt, dict):
                receipt_sig = receipt.get("signature")
                action_hash = receipt.get("action_hash")

            risk_dto = d.get("risk")
            risk_level = risk_dto.level if hasattr(risk_dto, "level") else (risk_dto.get("level") if isinstance(risk_dto, dict) else "LOW")
            created_at_val = d.get("created_at")
            if isinstance(created_at_val, datetime):
                created_at_str = created_at_val.isoformat()
            else:
                created_at_str = str(created_at_val or datetime.now(timezone.utc).isoformat())

            # Proveedor principal

            providers = d.get("providers", [])
            provider_str = "PolicyEngine"
            if providers and len(providers) > 0:
                first_p = providers[0]
                p_name = getattr(first_p, "provider_name", None) or (first_p.get("provider_name") if isinstance(first_p, dict) else "Supervisor")
                p_score = getattr(first_p, "confidence_score", None) or (first_p.get("confidence_score") if isinstance(first_p, dict) else None)
                if p_score is not None:
                    provider_str = f"{p_name} ({p_score:.2f})"
                else:
                    provider_str = str(p_name)

            results.append({
                "decision_id": d["decision_id"],
                "session_id": d["session_id"],
                "action_id": d["action_id"],
                "tool": tool_name or "system",
                "command": command,
                "description": description,
                "arguments": args,
                "status": d.get("status", "ALLOW"),
                "execution_mode": d.get("execution_mode", "local_restricted"),
                "risk_level": risk_level,
                "grounding_score": d.get("grounding_score", 0.0),
                "signature": receipt_sig,
                "action_hash": action_hash,
                "provider": provider_str,
                "created_at": created_at_str,
            })
        return sorted(results, key=lambda x: x["created_at"])

    # =========================================================================
    # CONFIRMACIÓN Y EJECUCIÓN FÍSICA
    # =========================================================================

    def confirm_decision(
        self,
        decision_id: str,
        approved: bool,
        reason: Optional[str] = None,
        actor: str = "human_operator",
        operator_id: Optional[str] = None,
        role: str = "operator",
        operator_token: Optional[str] = None,
        caller_is_verified_operator: bool = False,
        security_profile: Optional[str] = None,
    ) -> ConfirmDecisionResponse:
        """Autoriza o bloquea una decisión en espera de aprobación humana (REVIEW)."""
        with self._lock:
            record = self._decisions.get(decision_id)
        if not record:
            record = self.decision_repository.get(decision_id)
            if not record:
                raise KeyError(f"Decisión '{decision_id}' no encontrada.")
            with self._lock:
                self._decisions[decision_id] = record

        # VETO INCONDICIONAL: Una acción con veredicto BLOCK no puede ser autorizada manualmente ni saltarse la política de seguridad
        receipt: DecisionReceipt = record["receipt"]
        if approved and (record.get("status") == "BLOCK" or getattr(receipt, "decision_status", None) == DecisionStatus.BLOCK):
            raise PermissionError("Veto incondicional: Una acción con veredicto BLOCK no puede ser autorizada manualmente ni saltarse la política de seguridad.")

        if role == "viewer":
            raise PermissionError("El rol 'viewer' tiene permisos de solo lectura y no puede autorizar o rechazar decisiones.")

        if not approved and not (reason and reason.strip()):
            raise ValueError("Es obligatorio proporcionar un motivo justificado (reason) para rechazar una decisión en revisión.")

        # Finding 14: Verificación e integridad del operador en el servidor
        expected_secret = (
            os.getenv("PRAXEON_OPERATOR_KEY")
            or os.getenv("PRAXEON_SECRET_KEY")
            or os.getenv("PRAXEON_API_KEY")
        )
        is_verified_operator = caller_is_verified_operator
        if operator_token and expected_secret:
            if hmac.compare_digest(str(operator_token).strip(), str(expected_secret).strip()):
                is_verified_operator = True

        effective_profile = security_profile or get_active_security_profile()
        if is_auth_required(profile=effective_profile) or (effective_profile == "production"):
            if not is_verified_operator:
                raise PermissionError(
                    "La confirmación de decisiones en entorno seguro requiere autenticación de operador "
                    "en el servidor mediante un 'operator_token' válido (Finding 14)."
                )

        if role == "admin" and not is_verified_operator and expected_secret:
            raise PermissionError("Se requiere un 'operator_token' autenticado para ejercer la autoridad de rol 'admin'.")

        effective_role = role if (is_verified_operator or not expected_secret) else "operator"
        effective_operator_id = operator_id or ("verified_operator" if is_verified_operator else "operator_admin")

        session_id = record["session_id"]
        action: ActionCandidate = record["action"]
        now = datetime.now(timezone.utc)
        session_mode = record.get("execution_mode") or "local_restricted"

        if approved:
            new_status = "ALLOW"
            expires_at = now + timedelta(minutes=5)
            receipt_updated = receipt.model_copy(
                update={
                    "decision_status": DecisionStatus.ALLOW,
                    "expires_at": expires_at,
                    "execution_mode": ExecutionMode(session_mode),
                }
            )
            signed = sign_receipt(receipt_updated, self.policy_engine.secret_key)
            cap = signed.to_capability_payload(allowed_tools=[action.tool_call.tool_name] if action.tool_call else [])
            capability_dict = cap.model_dump(mode="json") if cap else None

            record["status"] = "ALLOW"
            record["receipt"] = signed
            record["capability"] = capability_dict
            record["expires_at"] = expires_at
            record["operator_id"] = effective_operator_id
            record["role"] = effective_role

            self.decision_repository.save(record)

            self.event_bus.emit(
                session_id=session_id,
                event_type=EventType.APPROVAL_COMPLETED,
                node_id=record["action_id"],
                decision_id=decision_id,
                payload={"approved": True, "reason": reason, "actor": actor, "operator_id": effective_operator_id, "role": effective_role},
            )
            self.event_bus.emit(
                session_id=session_id,
                event_type=EventType.CAPABILITY_ISSUED,
                node_id=record["action_id"],
                decision_id=decision_id,
                payload=capability_dict or {},
            )
            return ConfirmDecisionResponse(
                decision_id=decision_id,
                status="ALLOW",
                message="Decisión autorizada por operador humano. Capability emitido.",
                execution_mode=session_mode,
                operator_id=effective_operator_id,
                role=effective_role,
                capability=capability_dict,
                confirmed_at=now,
            )
        else:
            receipt: DecisionReceipt = record["receipt"]
            receipt_updated = receipt.model_copy(
                update={
                    "decision_status": DecisionStatus.BLOCK,
                    "execution_mode": ExecutionMode(session_mode),
                }
            )
            signed = sign_receipt(receipt_updated, self.policy_engine.secret_key)
            record["status"] = "BLOCKED"
            record["receipt"] = signed
            record["capability"] = None
            record["operator_id"] = effective_operator_id
            record["role"] = effective_role
            self.decision_repository.save(record)


            self.event_bus.emit(
                session_id=session_id,
                event_type=EventType.APPROVAL_COMPLETED,
                node_id=record["action_id"],
                decision_id=decision_id,
                payload={"approved": False, "reason": reason, "actor": actor, "operator_id": effective_operator_id, "role": effective_role},
            )
            self.event_bus.emit(
                session_id=session_id,
                event_type=EventType.DECISION_PRUNED,
                node_id=record["action_id"],
                decision_id=decision_id,
                payload={"reason": f"Rechazado por operador humano: {reason}"},
            )
            return ConfirmDecisionResponse(
                decision_id=decision_id,
                status="BLOCKED",
                message="Decisión rechazada por operador humano.",
                execution_mode=session_mode,
                operator_id=effective_operator_id,
                role=effective_role,
                capability=None,
                confirmed_at=now,
            )

    def reject_decision(
        self,
        decision_id: str,
        reason: str,
        actor: str = "human_operator",
        operator_id: Optional[str] = None,
        role: str = "operator",
        operator_token: Optional[str] = None,
        caller_is_verified_operator: bool = False,
        security_profile: Optional[str] = None,
    ) -> ConfirmDecisionResponse:
        """Rechaza formalmente una decisión en espera de aprobación humana (REVIEW)."""
        return self.confirm_decision(
            decision_id=decision_id,
            approved=False,
            reason=reason,
            actor=actor,
            operator_id=operator_id,
            role=role,
            operator_token=operator_token,
            caller_is_verified_operator=caller_is_verified_operator,
            security_profile=security_profile,
        )

    def execute_decision(
        self,
        decision_id: str,
        capability_token: Optional[Dict[str, Any]] = None,
        operator_id: Optional[str] = None,
        role: str = "operator",
    ) -> ExecuteDecisionResponse:
        """Ejecuta físicamente la herramienta autorizada en el sandbox o host."""
        if role == "viewer":
            raise PermissionError("El rol 'viewer' tiene permisos de solo lectura y no puede ejecutar decisiones.")

        with self._lock:
            record = self._decisions.get(decision_id)
        if not record:
            record = self.decision_repository.get(decision_id)
            if not record:
                raise KeyError(f"Decisión '{decision_id}' no encontrada.")
            with self._lock:
                self._decisions[decision_id] = record

        session_id = record["session_id"]
        action: ActionCandidate = record["action"]
        receipt: DecisionReceipt = record["receipt"]
        state = self.state_store.load_state(session_id) or SessionState(session_id=session_id, goal=Goal(objective="Task"))
        session_mode = record.get("execution_mode") or state.metadata.get("execution_mode", "local_restricted")

        # Regla 9: BLOCK decisions never reach execution
        if record.get("status") != "ALLOW":
            raise PolicyViolation(f"No se puede ejecutar una decisión en estado '{record.get('status')}'. Solo se permite la ejecución de decisiones 'ALLOW'.")

        # Regla 10: Replay protection at decision receipt level
        if receipt.is_executed:
            raise PolicyViolation("Esta decisión ya ha sido ejecutada previamente. Violación de replay protection.")

        # Regla 8: Validación de expiración temporal del capability
        if receipt.is_expired():
            raise PolicyViolation(
                f"Ejecución física DENEGADA: El capability ha expirado (expiró en: {receipt.expires_at.isoformat() if receipt.expires_at else 'N/A'})."
            )

        # Regla 7: Validar capability_token si se proporciona externamente
        if capability_token:
            if not verify_capability_signature(self.policy_engine.secret_key, capability_token):
                raise PolicyViolation("Firma HMAC del capability token inválida o manipulada.")
            token_mode = capability_token.get("execution_mode")
            if token_mode and token_mode != session_mode:
                raise PolicyViolation(f"Adulteración de execution_mode: el token especifica '{token_mode}', pero la sesión requiere '{session_mode}'.")
            exp = capability_token.get("expires_at")
            if exp:
                try:
                    exp_dt = datetime.fromisoformat(exp) if isinstance(exp, str) else exp
                    if exp_dt:
                        now_utc = datetime.now(timezone.utc)
                        if exp_dt.tzinfo is None:
                            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                        if now_utc > exp_dt:
                            raise PolicyViolation(f"Ejecución física DENEGADA: El capability token ha expirado (expiró en: {exp_dt.isoformat()}).")
                except PolicyViolation:
                    raise
                except Exception:
                    pass

        if session_mode == "full_access":
            logger.warning(
                "[SECURITY AUDIT] Full Access physical execution started: Session '%s', Action '%s' executed directly on Host OS (unconfined).",
                session_id,
                action.tool_call.tool_name if action.tool_call else "none",
            )

        # Emitir evento: execution.started
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.EXECUTION_STARTED,
            node_id=record["action_id"],
            decision_id=decision_id,
            payload={
                "tool": action.tool_call.tool_name if action.tool_call else None,
                "arguments": action.tool_call.arguments if action.tool_call else {},
                "execution_mode": session_mode,
                "sandboxed": session_mode != "full_access",
                "isolation": "None (Host OS)" if session_mode == "full_access" else "Active",
                "operator_id": operator_id,
            },
        )

        try:
            # Ejecución formal con SecureExecutor (verifica nonce atómico y firma HMAC)
            observation = self.executor.execute(
                action=action,
                state=state,
                receipt=receipt,
                decision=PolicyDecision(status=DecisionStatus.ALLOW),
            )
            # Marcar recibo como ejecutado
            updated_receipt = receipt.model_copy(
                update={"is_executed": True, "execution_timestamp": datetime.now(timezone.utc)}
            )
            record["receipt"] = updated_receipt
            self.decision_repository.save(record)


            # Actualizar observación en el estado de la sesión
            current_state = self.state_store.load_state(session_id)
            if current_state:
                if current_state.steps and (
                    current_state.steps[-1].id == record["action_id"]
                    or (hasattr(current_state.steps[-1], "action") and current_state.steps[-1].action and current_state.steps[-1].action.id == record["action_id"])
                ):
                    last_step = current_state.steps[-1]
                    current_state.steps[-1] = StepRecord(
                        id=record["action_id"],
                        index=last_step.index,
                        action=last_step.action,
                        decision=last_step.decision,
                        observation=observation.output[:500],
                        timestamp=last_step.timestamp,
                    )
                else:
                    current_state.steps.append(
                        StepRecord(
                            id=record["action_id"],
                            index=len(current_state.steps),
                            action=action,
                            decision=PolicyDecision(status=DecisionStatus.ALLOW),
                            observation=observation.output[:500],
                        )
                    )
                self.state_store.save_state(current_state)

            # Emitir eventos de culminación
            self.event_bus.emit(
                session_id=session_id,
                event_type=EventType.EXECUTION_COMPLETED,
                node_id=record["action_id"],
                decision_id=decision_id,
                payload={
                    "success": observation.success,
                    "exit_code": getattr(observation, "exit_code", 0 if observation.success else 1),
                    "execution_time_ms": observation.execution_time_ms,
                    "execution_mode": session_mode,
                    "sandboxed": session_mode != "full_access",
                },
            )
            self.event_bus.emit(
                session_id=session_id,
                event_type=EventType.OBSERVATION_RECORDED,
                node_id=record["action_id"],
                decision_id=decision_id,
                payload={"output": observation.output[:100_000] if len(observation.output) > 100_000 else observation.output},
            )

            # Si es finish o herramienta de conclusión de ciclo de vida
            if action.tool_call and action.tool_call.tool_name in ("finish", "complete_task", "done", "complete", "task_completed") and observation.success:
                summary = (
                    action.tool_call.arguments.get("summary")
                    or action.tool_call.arguments.get("final_answer")
                    or observation.output.replace("Tarea concluida: ", "")
                    or "Misión finalizada exitosamente."
                )
                self.event_bus.emit(
                    session_id=session_id,
                    event_type=EventType.SESSION_COMPLETED,
                    node_id=f"root_{session_id}",
                    payload={"status": "completed", "summary": summary},
                )
                with self._lock:
                    if session_id in self._sessions_meta:
                        self._sessions_meta[session_id]["status"] = "Completed"
                        self._sessions_meta[session_id]["final_answer"] = summary

            tier_name = (
                "full_access" if session_mode == "full_access"
                else ("container" if session_mode == "container" else "local_process")
            )

            return ExecuteDecisionResponse(
                decision_id=decision_id,
                action_id=record["action_id"],
                output=observation.output,
                success=observation.success,
                exit_code=getattr(observation, "exit_code", 0 if observation.success else 1),
                execution_time_ms=observation.execution_time_ms,
                execution_mode=session_mode,
                tier=tier_name,
                fallback_occurred=False,
                is_error=observation.is_error,
            )
        except PolicyViolation as pv:
            self.event_bus.emit(
                session_id=session_id,
                event_type=EventType.EXECUTION_COMPLETED,
                node_id=record["action_id"],
                decision_id=decision_id,
                payload={"success": False, "error": str(pv)},
            )
            raise

    # =========================================================================
    # EJECUCIÓN INTERACTIVA EN TIEMPO REAL (Live Mission Runner)
    # =========================================================================

    def start_mission(
        self,
        goal: str,
        session_id: Optional[str] = None,
        agent_name: str = "CodingAgent",
        execution_mode: str = "local_restricted",
        workspace_root: Optional[str] = None,
        llm_provider: str = "simulator",
        llm_model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        supervisor: str = "laya",
        max_steps: int = 25,
        step_delay_ms: int = 900,
        autonomous: bool = False,
        allow_unattended_execution: bool = False,
        llm_failure_policy: str = "synthetic_fallback",
        chat_history: Optional[List[Dict[str, str]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Inicia una misión interactiva supervisada en tiempo real."""
        sid = session_id or f"s-{uuid.uuid4().hex[:8]}"

        # Configurar proveedor supervisor semántico si difiere
        if supervisor.lower() in ("laya", "laya-system1", "laya-v1"):
            self.provider = LayaProvider(backend="auto")
        else:
            self.provider = TypeSafeAdapter(
                api_key=self.config.provider.api_key,
                model_name=self.config.provider.model,
            )

        effective_ws = workspace_root or get_default_workspace_root()
        unattended = bool(autonomous or allow_unattended_execution)

        sess_metadata = {
            "llm_provider": llm_provider,
            "llm_model": llm_model or "default",
            "supervisor": supervisor,
            "max_steps": max_steps,
            "execution_mode": execution_mode,
            "workspace_root": effective_ws,
            "working_directory": effective_ws,
            "autonomous": unattended,
            "allow_unattended_execution": unattended,
            "llm_failure_policy": llm_failure_policy,
        }
        if metadata:
            sess_metadata.update(metadata)

        meta = self.create_session(
            goal=goal,
            session_id=sid,
            agent_name=agent_name,
            execution_mode=execution_mode,
            workspace_root=effective_ws,
            metadata=sess_metadata,
        )

        full_access_auth = bool(
            (meta.get("metadata") or {}).get("full_access_authorized_by_operator", False)
        )

        mission_state = {
            "session_id": sid,
            "goal": goal,
            "agent_name": agent_name,
            "execution_mode": execution_mode,
            "workspace_root": effective_ws,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "api_key": api_key,
            "base_url": base_url,
            "supervisor": supervisor,
            "max_steps": max_steps,
            "step_delay_ms": step_delay_ms,
            "paused": False,
            "stopped": False,
            "current_step": 0,
            "autonomous": unattended,
            "allow_unattended_execution": unattended,
            "full_access_authorized_by_operator": full_access_auth,
            "llm_failure_policy": llm_failure_policy,
            "chat_history": chat_history,
        }

        with self._lock:
            self._running_missions[sid] = mission_state

        worker_thread = threading.Thread(
            target=self._run_mission_worker,
            args=(mission_state,),
            daemon=True,
            name=f"mission-worker-{sid}",
        )
        worker_thread.start()

        return meta

    def pause_mission(self, session_id: str) -> bool:
        """Pausa temporalmente la ejecución interactiva de una misión."""
        with self._lock:
            if session_id in self._running_missions:
                self._running_missions[session_id]["paused"] = True
                return True
        return False

    def resume_mission(self, session_id: str) -> bool:
        """Reanuda la ejecución interactiva de una misión en pausa."""
        with self._lock:
            if session_id in self._running_missions:
                self._running_missions[session_id]["paused"] = False
                return True
        return False

    def stop_mission(self, session_id: str) -> bool:
        """Detiene de forma definitiva la ejecución interactiva de una misión."""
        with self._lock:
            if session_id in self._running_missions:
                self._running_missions[session_id]["stopped"] = True
                return True
        return False

    def rollback_session(self, session_id: str, checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
        """Revierte el estado de una sesión al último checkpoint válido o al checkpoint especificado."""
        state = self.state_store.load_state(session_id)
        if not state:
            raise KeyError(f"Sesión '{session_id}' no encontrada para reversión.")

        target_chk = checkpoint_id or (f"chk_{len(state.steps)}" if state.steps else "genesis")
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.INTERVENTION_APPLIED,
            node_id=f"rollback_{session_id}",
            payload={"action": "rollback", "checkpoint_id": target_chk},
        )
        return {"session_id": session_id, "checkpoint_id": target_chk, "status": "RolledBack"}

    def _run_mission_worker(self, mission: Dict[str, Any]) -> None:
        """Worker asíncrono que genera y propone pasos interactivos para la sesión.
        
        Soporta modelos LLM reales (Ollama, Groq, OpenAI, Gemini, OpenRouter) con bucle ReAct
        completo, y un planificador contextual dinámico adaptado estrictamente al objetivo del usuario.
        """
        from praxeon.agent_llm import BaseAgentLLM, SimulatedAgentLLM, create_agent_llm
        from praxeon.live_agent import parse_llm_steps

        sid = mission["session_id"]
        goal = mission["goal"]
        provider_name = (mission.get("llm_provider") or "simulator").lower().strip()
        model_name = mission.get("llm_model")
        api_key = mission.get("api_key")
        base_url = mission.get("base_url")
        max_steps = mission.get("max_steps", 25)
        delay_sec = max(0.2, mission.get("step_delay_ms", 900) / 1000.0)

        # Resolver modo de ejecución de la sesión (Full Access vs Container vs Local Restricted)
        session_mode = mission.get("execution_mode") or "local_restricted"
        with self._lock:
            if sid in self._sessions_meta:
                session_mode = self._sessions_meta[sid].get("execution_mode") or session_mode
        if not session_mode or session_mode == "local_restricted":
            st = self.state_store.load_state(sid)
            if st and st.metadata:
                session_mode = st.metadata.get("execution_mode", session_mode)

        # Retardo inicial para dar tiempo al WebSocket a suscribirse
        time.sleep(0.4)

        # 1. Intentar inicializar cliente LLM real si se solicitó un proveedor online/local
        agent_llm: Optional[BaseAgentLLM] = None
        use_real_llm = False

        if provider_name not in ("simulator", "mock", "sim"):
            try:
                llm_timeout = float(os.getenv("PRAXEON_LLM_TIMEOUT", "180.0"))
                agent_llm = create_agent_llm(
                    provider=provider_name,
                    model=model_name,
                    api_key=api_key,
                    base_url=base_url,
                    timeout=llm_timeout,
                )
                if not isinstance(agent_llm, SimulatedAgentLLM):
                    use_real_llm = True
                    logger.info("Misión inicializada con LLM real: %s (%s)", agent_llm.provider_name, agent_llm.model_name)
            except Exception as err:
                llm_fail_policy = (mission.get("llm_failure_policy") or "synthetic_fallback").lower().strip()
                if llm_fail_policy == "fail_closed":
                    logger.error(
                        "No se pudo inicializar proveedor LLM '%s': %s bajo política fail_closed.",
                        provider_name,
                        err,
                    )
                    self.event_bus.emit(
                        session_id=sid,
                        event_type=EventType.INTERVENTION_APPLIED,
                        node_id=f"root_{sid}",
                        payload={
                            "error": f"Fallo al inicializar proveedor LLM '{provider_name}' bajo política fail_closed: {err}",
                            "fatal": True,
                            "llm_failure_policy": "fail_closed",
                        },
                    )
                    mission["stopped"] = True
                    return

                logger.warning(
                    "No se pudo inicializar proveedor LLM '%s': %s. Se activará el planificador contextual dinámico en modo degradado.",
                    provider_name,
                    err,
                )
                self.event_bus.emit(
                    session_id=sid,
                    event_type=EventType.INTERVENTION_APPLIED,
                    node_id=f"root_{sid}",
                    payload={
                        "warning": f"[DEGRADED_MODE: SYNTHETIC_PLANNER] LLM '{provider_name}' no disponible ({err}). Activando razonamiento contextual dinámico adaptado a: '{goal}'.",
                        "degraded_mode": True,
                        "planner": "synthetic",
                    },
                )

        step_idx = 0

        # =========================================================================
        # MODO A: LLM REAL (Ollama, Groq, OpenRouter, OpenAI, Gemini)
        # =========================================================================
        if use_real_llm and agent_llm is not None:
            # Recolectar información detallada del sistema operativo y espacio de trabajo real
            ws_root = mission.get("workspace_root") or get_default_workspace_root()
            from praxeon.core.session_context import SessionContextManager
            ctx_mgr = SessionContextManager()
            env_info = ctx_mgr.get_environment_info(root_dir=ws_root)

            system_prompt = (
                "Eres un asistente y agente de software autónomo supervisado cognitivamente en tiempo real por PRAXEON (JEV Reasoning Navigator).\n"
                f"OBJETIVO O PREGUNTA DEL USUARIO: {goal}\n"
                f"DIRECTORIO DE TRABAJO BASE PARA ESTA SESIÓN: '{ws_root}'\n\n"
                f"{env_info}\n\n"
                "FORMATO DE RESPUESTA EN CADA TURNO (Estricto ReAct):\n"
                "Thought: <análisis concreto de lo que vas a hacer y por qué>\n"
                "Action: <herramienta>(<argumentos_en_json_o_string>)\n\n"
                "Herramientas disponibles:\n"
                "- read_file(path: str) -> Lee el contenido de un archivo del espacio de trabajo local si la tarea requiere auditar código o configuración existente.\n"
                "- edit_file(path: str, diff: str) -> Aplica modificaciones a un archivo en el espacio de trabajo local cuando la tarea pide editar código existente.\n"
                "- run_command(command: str) -> Ejecuta un comando en la consola del SO (PowerShell en Windows, bash en Unix).\n"
                "- git(command: str) -> Ejecuta comandos git en el repositorio (status, diff, log, etc.).\n"
                "- finish(summary: str) -> Concluye entregando la respuesta directa a la pregunta, especificación, diseño, informe o solución solicitada por el usuario.\n\n"
                "PRINCIPIOS OPERATIVOS Y RAZONAMIENTO COGNITIVO:\n"
                "1. COMPRENSIÓN DEL ÁMBITO DEL PROYECTO (ANTI-SESGO DETERMINISTA): Atiende estrictamente a lo que el usuario solicita. Si el usuario te pide un informe, especificación, arquitectura, estructura de carpetas, requisitos funcionales/no funcionales o user stories para OTRO proyecto, una nueva aplicación o una propuesta de software conceptual, NO asumas jamás que se refiere al proyecto local de PRAXEON ni leas el README local. Elabora y entrega el diseño o informe completo de dicho proyecto directamente mediante finish(summary=\"...\").\n"
                "2. SIN RESPUESTAS NI ASUNCIONES DETERMINISTAS: Palabras como 'proyecto', 'informe', 'documentación', 'guía' o 'estructura' NO deben disparar de forma automática la lectura de README.md ni pyproject.toml. Analiza dinámicamente si la tarea es de formulación y diseño (responde directamente con finish) o si solicita explícitamente auditar código de este repositorio local.\n"
                "3. PREGUNTAS Y VALORACIONES: Si te consultan tu opinión, calificación técnica o análisis conceptual sobre cualquier tema o proyecto, responde de forma crítica, sincera y fundamentada directamente con finish(summary=\"...\").\n"
                "4. TAREAS DE CÓDIGO TÉCNICO SOBRE EL REPOSITORIO LOCAL: Solo si la tarea solicita implementar código, corregir un bug o ejecutar pruebas sobre archivos existentes de este entorno, utiliza read_file, edit_file o run_command.\n"
                "5. COMPATIBILIDAD DE SO: En Windows, NO uses comandos Unix/Linux como 'ls', 'cat', 'grep'. Utiliza 'read_file(path)' o comandos de PowerShell en run_command.\n"
                "6. RETROCESO: Si una acción falla o es vetada por el supervisor, reflexiona en 'Thought:' y propone una alternativa válida."
            )

            conversation: List[Dict[str, str]] = []
            chat_hist = mission.get("chat_history") or []
            for h_item in chat_hist:
                if isinstance(h_item, dict) and "role" in h_item and "content" in h_item:
                    h_role = "assistant" if h_item.get("role") == "assistant" else "user"
                    h_txt = str(h_item.get("content") or "").strip()
                    if h_txt:
                        conversation.append({"role": h_role, "content": h_txt})

            conversation.append({
                "role": "user",
                "content": (
                    f"Tarea/Pregunta: {goal}\n\n"
                    "Responde directamente a mi petición concreta con tu criterio técnico. "
                    "Si es una pregunta, opinión, diseño conceptual, especificación, user stories, requisitos o informe de proyecto, entrégala directamente con finish(summary=...). "
                    "Solo si requieres interactuar con código o archivos existentes de este entorno local (modificar o probar), utiliza las herramientas pertinentes sobre los archivos reales."
                ),
            })

            active_parent_id = f"root_{sid}"
            consecutive_failures = 0
            technical_failures = 0
            semantic_fixations = 0
            recent_action_signatures: List[str] = []
            circuit_breaker_triggered = False
            llm_fail_policy = (mission.get("llm_failure_policy") or "synthetic_fallback").lower().strip()

            while step_idx < max_steps:
                if mission.get("stopped"):
                    break

                while mission.get("paused") and not mission.get("stopped"):
                    time.sleep(0.2)

                step_idx += 1
                mission["current_step"] = step_idx

                # Invocar LLM real
                llm_output = ""
                try:
                    llm_output = agent_llm.generate(conversation, system_prompt=system_prompt)
                    technical_failures = 0  # Éxito técnico: resetear contador técnico
                except Exception as gen_err:
                    technical_failures += 1
                    logger.warning("Error durante generación con LLM '%s': %s", provider_name, gen_err)

                    if technical_failures >= 3:
                        self.event_bus.emit(
                            session_id=sid,
                            event_type=EventType.INTERVENTION_APPLIED,
                            node_id=f"root_{sid}",
                            payload={
                                "intervention": "TECHNICAL_CIRCUIT_BREAKER",
                                "message": f"⚡ PRAXEON TECHNICAL CIRCUIT BREAKER: Fallos técnicos consecutivos ({technical_failures}) con el proveedor LLM '{provider_name}'. Circuito técnico ABIERTO para prevenir saturación.",
                                "consecutive_technical_failures": technical_failures,
                            },
                        )
                        mission["stopped"] = True
                        break

                    if llm_fail_policy == "fail_closed":
                        self.event_bus.emit(
                            session_id=sid,
                            event_type=EventType.INTERVENTION_APPLIED,
                            node_id=f"root_{sid}",
                            payload={
                                "error": f"Fallo de generación en LLM '{provider_name}' bajo política fail_closed: {gen_err}",
                                "fatal": True,
                                "llm_failure_policy": "fail_closed",
                            },
                        )
                        mission["stopped"] = True
                        break

                    self.event_bus.emit(
                        session_id=sid,
                        event_type=EventType.INTERVENTION_APPLIED,
                        node_id=f"root_{sid}",
                        payload={
                            "warning": f"[DEGRADED_MODE: SYNTHETIC_PLANNER] Fallo temporal de LLM '{provider_name}' ({gen_err}). Activando contingencia contextual.",
                            "degraded_mode": True,
                            "planner": "synthetic",
                        },
                    )
                    steps_backup = generate_goal_tailored_steps(goal=goal, max_steps=max_steps)
                    backup_idx = min(step_idx - 1, len(steps_backup) - 1)
                    b_step = steps_backup[backup_idx] if (steps_backup and backup_idx >= 0) else {
                        "tool_name": "run_command",
                        "tool_args": {"command": "python -c \"print('Paso de inspección segura')\""},
                        "thought_rationale": "Paso de contingencia tras fallo del proveedor LLM.",
                    }
                    llm_output = f"Thought: [DEGRADED_MODE: SYNTHETIC_PLANNER] [Contingencia por fallo temporal en {provider_name}]: {b_step.get('thought_rationale', '')}\nAction: {b_step.get('tool_name', 'run_command')}({json.dumps(b_step.get('tool_args', {}))})"

                # Parsear Thought + Action
                parsed_steps = parse_llm_steps(llm_output)
                if parsed_steps:
                    st = parsed_steps[0]
                    raw_tool = (st.get("tool_name") or "").strip().lower()
                    if raw_tool in ("action", "undefined", "none", "null", "step", ""):
                        # Degradación sintáctica del modelo
                        tool = "run_command"
                        args = {"command": "python -c \"print('Paso de inspección segura')\""}
                        thought = "El modelo emitió una herramienta no especificada ('undefined'). El supervisor redirige a inspección segura."
                    else:
                        tool = raw_tool
                        args = st.get("tool_args") or {}
                        thought = st.get("thought_rationale") or f"Paso {step_idx} generado por {agent_llm.model_name} para '{goal}'."
                else:
                    finish_match = re.search(
                        r'finish\s*\(\s*(?:summary\s*=\s*)?(?:"""(.*?)"""|\'\'\'(.*?)\'\'\'|"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|(.*?))\s*\)',
                        llm_output,
                        re.DOTALL,
                    )
                    if finish_match:
                        raw_summary = (
                            finish_match.group(1)
                            or finish_match.group(2)
                            or finish_match.group(3)
                            or finish_match.group(4)
                            or finish_match.group(5)
                            or ""
                        )
                        tool = "finish"
                        args = {"summary": raw_summary.strip()}
                        thought = "Conclusión directa emitida por el agente."
                    elif any(w in llm_output.lower() for w in ("finish", "complet", "conclu", "finaliz", "resuelt", "respuesta", "opini")):
                        clean_ans = llm_output
                        if "thought:" in llm_output.lower():
                            parts = re.split(r"(?i)thought\s*:", llm_output)
                            clean_ans = parts[-1].strip()
                        tool = "finish"
                        args = {"summary": clean_ans.strip()}
                        thought = "Conclusión directa emitida por el agente."
                    else:
                        is_technical_action = any(k in goal.lower() for k in ("test", "pytest", "bug", "error", "edit", "modific", "implement", "crea"))
                        if not is_technical_action:
                            clean_ans = llm_output
                            if "thought:" in llm_output.lower():
                                parts = re.split(r"(?i)thought\s*:", llm_output)
                                clean_ans = parts[-1].strip()
                            tool = "finish"
                            args = {"summary": clean_ans.strip()}
                            thought = "Respuesta directa emitida por el agente."
                        else:
                            tool = "run_command"
                            args = {"command": "python -c \"print('Paso de inspección ejecutado')\""}
                            thought = llm_output[:250].strip() or f"Paso {step_idx} propuesto para: '{goal}'."

                # CIRCUITO DE FIJACIÓN SEMÁNTICA (SEMANTIC CIRCUIT BREAKER): Detección de bucles repetitivos de herramientas
                action_sig = f"{tool}:{json.dumps(args, sort_keys=True)}"
                if recent_action_signatures and recent_action_signatures[-1] == action_sig:
                    semantic_fixations += 1
                else:
                    semantic_fixations = 0
                recent_action_signatures.append(action_sig)
                if len(recent_action_signatures) > 10:
                    recent_action_signatures.pop(0)

                if semantic_fixations >= 2:
                    self.event_bus.emit(
                        session_id=sid,
                        event_type=EventType.INTERVENTION_APPLIED,
                        node_id=f"act_{step_idx}",
                        parent_id=active_parent_id,
                        payload={
                            "intervention": "SEMANTIC_CIRCUIT_BREAKER_LOOP_PRUNED",
                            "message": f"⚡ PRAXEON SEMANTIC CIRCUIT BREAKER: Fijación semántica / bucle del agente detectado en '{tool}'. El supervisor poda la rama y fuerza REPLAN.",
                            "action_signature": action_sig,
                            "backtrack_to": active_parent_id,
                        },
                    )
                    conversation.append({
                        "role": "user",
                        "content": (
                            f"🚨 [SUPERVISOR PRAXEON - SEMANTIC CIRCUIT BREAKER]: Bucle de fijación semántica detectado.\n"
                            f"Has propuesto '{tool}' con argumentos idénticos repetidamente sin avance comprobable. Esta rama queda PODADA.\n"
                            "DEBES formular una alternativa de razonamiento diferente, inspeccionar otros archivos o cambiar tu estrategia."
                        ),
                    })
                    semantic_fixations = 0
                    time.sleep(delay_sec)
                    continue

                operation = f"{step_idx}. {tool}"
                action_node_id = f"act_{step_idx}"

                req = ProposeActionRequest(
                    action_id=action_node_id,
                    parent_id=active_parent_id,
                    tool=tool,
                    operation=operation,
                    arguments=args,
                    thought_rationale=thought,
                    provenance={
                        "source": f"LLM ({agent_llm.provider_name.upper()} - {agent_llm.model_name})",
                        "step": step_idx,
                    },
                    context={"goal": goal},
                )

                obs_output = ""
                executed_successfully = False
                try:
                    resp = self.propose_action(session_id=sid, proposal=req)

                    if resp.status == "BLOCK":
                        executed_successfully = False
                        obs_output = f"Acción clasificada como PELIGROSA o destructiva por seguridad: {', '.join(resp.policy.reason_codes or ['Veto operacional'])}"
                    elif resp.status == "REPLAN":
                        executed_successfully = False
                        obs_output = f"Acción clasificada como INNECESARIA, desvío o bucle por el supervisor JEV-LAYA: {', '.join(resp.policy.reason_codes or ['Poda cognitiva'])}"
                    # Si requiere confirmación humana (ej. REVIEW por git push)
                    elif resp.status == "REVIEW" or resp.policy.requires_confirmation:
                        allow_unattended = bool(
                            mission.get("allow_unattended_execution")
                            or mission.get("autonomous")
                            or False
                        )
                        full_access_authorized = bool(
                            mission.get("full_access_authorized_by_operator")
                            or mission.get("operator_authorized")
                        )
                        if session_mode == "full_access" and allow_unattended and full_access_authorized:
                            # Auto-confirmación sólo si explícitamente se configuró allow_unattended_execution / autonomous
                            logger.warning(
                                "[SECURITY AUDIT] Auto-confirmando decisión %s en modo FULL_ACCESS_AUTONOMOUS...",
                                resp.decision_id,
                            )
                            try:
                                conf_res = self.confirm_decision(
                                    decision_id=resp.decision_id,
                                    approved=True,
                                    reason="Auto-autorizado por consentimiento explícito de sesión en modo Full Access Autónomo",
                                    operator_id="operator_full_access_auto",
                                    role="operator",
                                )
                                if conf_res.status == "ALLOW":
                                    exec_res = self.execute_decision(decision_id=resp.decision_id)
                                    obs_output = exec_res.output or ""
                                    executed_successfully = exec_res.success and not exec_res.is_error
                            except Exception as ex:
                                obs_output = f"Error en ejecución Full Access: {ex}"
                                executed_successfully = False
                        else:
                            wait_count = 0
                            while wait_count < 120 and not mission.get("stopped"):
                                time.sleep(0.5)
                                wait_count += 1
                                with self._lock:
                                    dec = self._decisions.get(resp.decision_id)
                                    if dec and dec.get("status") in ("ALLOW", "BLOCKED"):
                                        break
                            with self._lock:
                                dec = self._decisions.get(resp.decision_id)
                                dec_status = dec.get("status") if dec else None
                            if dec_status == "ALLOW":
                                try:
                                    exec_res = self.execute_decision(decision_id=resp.decision_id)
                                    obs_output = exec_res.output or ""
                                    executed_successfully = exec_res.success and not exec_res.is_error
                                except Exception as ex:
                                    logger.debug("Execution note: %s", ex)
                                    obs_output = str(ex)
                    elif resp.status == "ALLOW":
                        try:
                            exec_res = self.execute_decision(decision_id=resp.decision_id)
                            obs_output = exec_res.output or ""
                            executed_successfully = exec_res.success and not exec_res.is_error
                        except Exception as ex:
                            logger.debug("Execution note: %s", ex)
                            obs_output = str(ex)
                except Exception as err:
                    logger.error(f"Error proponiendo paso {step_idx} en sesión {sid}: {err}")
                    obs_output = str(err)
                    executed_successfully = False

                if executed_successfully:
                    # Acción exitosa: el cursor activo del árbol avanza
                    consecutive_failures = 0
                    active_parent_id = action_node_id
                    conversation.append({
                        "role": "assistant",
                        "content": f"Thought: {thought}\nAction: {tool}({json.dumps(args, ensure_ascii=False)})",
                    })
                    raw_obs = obs_output or "Acción ejecutada correctamente."
                    if len(raw_obs) > 50_000:
                        keep_h = 35_000
                        keep_t = 15_000
                        omitted = len(raw_obs) - 50_000
                        llm_obs = f"{raw_obs[:keep_h]}\n\n[... Truncado: {omitted} caracteres intermedios omitidos por JEV para optimizar contexto del LLM ...]\n\n{raw_obs[-keep_t:]}"
                    else:
                        llm_obs = raw_obs

                    conversation.append({
                        "role": "user",
                        "content": (
                            f"Observación de {tool}:\n{llm_obs}\n\n"
                            f"[Supervisión]: Responde de forma directa, natural y enfocada a la petición del usuario: '{goal}'. "
                            "Si es una especificación, diseño, requisitos o informe de otro proyecto o arquitectura, responde a ese proyecto sin confundirlo con el repositorio local."
                        ),
                    })
                else:
                    consecutive_failures += 1

                    # RETROCESO (BACKTRACK) Y BIFURCACIÓN:
                    self.event_bus.emit(
                        session_id=sid,
                        event_type=EventType.INTERVENTION_APPLIED,
                        node_id=action_node_id,
                        parent_id=active_parent_id,
                        payload={
                            "intervention": "BACKTRACK_AND_BRANCH",
                            "message": f"Fallo o veto en '{action_node_id}'. El supervisor realiza un retroceso a '{active_parent_id}' para bifurcar una hipótesis alternativa.",
                            "backtrack_to": active_parent_id,
                            "failed_node": action_node_id,
                        },
                    )
                    conversation.append({
                        "role": "assistant",
                        "content": f"Thought: {thought}\nAction: {tool}({json.dumps(args, ensure_ascii=False)})",
                    })

                    # CIRCUITO DE BLOQUEO Y DETECCIÓN DE BUCLE PERSISTENTE (JEV CIRCUIT BREAKER)
                    if consecutive_failures >= 2 and not circuit_breaker_triggered:
                        circuit_breaker_triggered = True
                        cwd = os.getcwd()
                        real_files = []
                        try:
                            real_files = [f for f in os.listdir(cwd) if not f.startswith(".")][:12]
                        except Exception:
                            real_files = ["praxeon", "web", "tests"]
                        real_files_str = ", ".join(real_files)

                        self.event_bus.emit(
                            session_id=sid,
                            event_type=EventType.INTERVENTION_APPLIED,
                            node_id=action_node_id,
                            parent_id=f"root_{sid}",
                            payload={
                                "intervention": "CIRCUIT_BREAKER_GROUNDING_INJECTION",
                                "message": f"⚡ JEV CIRCUIT BREAKER: Bucle de alucinación/fallos consecutivos ({consecutive_failures}) intentando acceder a archivos/scripts inexistentes. El supervisor poda la rama, fuerza retroceso a la raíz e inyecta la estructura real del proyecto.",
                                "real_files": real_files,
                                "backtrack_to": f"root_{sid}",
                            },
                        )

                        conversation.append({
                            "role": "user",
                            "content": (
                                f"🚨 [INTERVENCIÓN JEV - CIRCUIT BREAKER ACTIVADO]:\n"
                                f"Has acumulado {consecutive_failures} acciones fallidas o vetadas intentando acceder a archivos o scripts inexistentes.\n"
                                f"El supervisor ha PODADO esa rama inválida y forzado un RETROCESO al nodo raíz.\n\n"
                                f"ARCHIVOS Y CARPETAS REALES EN EL ENTORNO ANFITRIÓN:\n"
                                f"[{real_files_str}]\n\n"
                                "DIRECTIVA ESTRICTA DEL SUPERVISOR:\n"
                                "1. NO intentes inventar nombres de archivos ni scripts inexistentes.\n"
                                "2. Si la tarea requiere interactuar con el código de este espacio de trabajo anfitrión, trabaja exclusivamente sobre rutas reales comprobadas.\n"
                                "3. Si la tarea es de diseño, especificación conceptual o sobre otro proyecto, entrega tu solución o informe directamente mediante 'finish'."
                            ),
                        })
                        active_parent_id = f"root_{sid}"
                        time.sleep(delay_sec)
                        continue

                    elif consecutive_failures >= 4:
                        logger.warning("Terminación preventiva por supervisor JEV en sesión %s tras 4 fallos continuos.", sid)
                        summary_final = f"Misión concluida preventivamente por el supervisor JEV para detener bucle de alucinación tras {step_idx} turnos. El agente insistió repetidamente en recursos no fundamentados."
                        self.event_bus.emit(
                            session_id=sid,
                            event_type=EventType.INTERVENTION_APPLIED,
                            node_id=action_node_id,
                            parent_id=f"root_{sid}",
                            payload={
                                "intervention": "SUPERVISOR_FORCED_TERMINATION",
                                "message": "Supervisión JEV: Se cortó la ejecución para evitar un bucle de alucinación infinito. Misión concluida de forma segura.",
                            },
                        )
                        self.event_bus.emit(
                            session_id=sid,
                            event_type=EventType.SESSION_COMPLETED,
                            node_id=f"root_{sid}",
                            payload={"status": "completed", "summary": summary_final},
                        )
                        with self._lock:
                            if sid in self._sessions_meta:
                                self._sessions_meta[sid]["status"] = "Completed"
                                self._sessions_meta[sid]["final_answer"] = summary_final
                        break
                    else:
                        conversation.append({
                            "role": "user",
                            "content": (
                                f"[ALERTA SUPERVISOR PRAXEON]: La acción {tool} no tuvo éxito ({obs_output[:350]}).\n"
                                f"El supervisor ha aplicado un RETROCESO (Backtrack) al nodo '{active_parent_id}'. "
                                "Formula una HIPÓTESIS ALTERNATIVA (Bifurcación) para abordar el objetivo por otra vía. "
                                "Explica tu retroceso en 'Thought:' y propone tu nueva 'Action:'."
                            ),
                        })

                if tool in ("finish", "complete_task", "done", "complete", "task_completed"):
                    if executed_successfully:
                        final_ans = (
                            args.get("summary")
                            or args.get("final_answer")
                            or obs_output.replace("Tarea concluida: ", "")
                        )
                        with self._lock:
                            if sid in self._sessions_meta:
                                self._sessions_meta[sid]["final_answer"] = final_ans
                    break

                time.sleep(delay_sec)

        # =========================================================================
        # MODO B: PLANIFICADOR CONTEXTUAL DINÁMICO (Simulator o Fallback de Provider)
        # =========================================================================
        else:
            steps_to_run = generate_goal_tailored_steps(goal=goal, max_steps=max_steps)
            active_parent_id = f"root_{sid}"

            while step_idx < len(steps_to_run):
                if mission.get("stopped"):
                    break

                while mission.get("paused") and not mission.get("stopped"):
                    time.sleep(0.2)

                step_data = steps_to_run[step_idx]
                step_idx += 1
                mission["current_step"] = step_idx

                # Usar parent_id explícito del plan o el active_parent_id actual
                target_parent = step_data.get("parent_id") or active_parent_id
                action_node_id = f"act_{step_idx}"
                thought_str = step_data["thought"]
                prov_source = f"LLM ({provider_name.upper()})"
                if provider_name not in ("simulator", "mock", "sim"):
                    thought_str = f"[DEGRADED_MODE: SYNTHETIC_PLANNER] {thought_str}"
                    prov_source = "SYNTHETIC_FALLBACK"

                req = ProposeActionRequest(
                    action_id=action_node_id,
                    parent_id=target_parent,
                    tool=step_data["tool"],
                    operation=step_data["operation"],
                    arguments=step_data["arguments"],
                    thought_rationale=thought_str,
                    provenance={
                        "source": prov_source,
                        "step": step_idx,
                    },
                    context={"goal": goal},
                )

                executed_successfully = False
                try:
                    resp = self.propose_action(session_id=sid, proposal=req)

                    # Si requiere confirmación humana (ej. REVIEW por git push), esperar a que sea autorizada
                    if resp.status == "REVIEW" or resp.policy.requires_confirmation:
                        allow_unattended = bool(
                            mission.get("allow_unattended_execution")
                            or mission.get("autonomous")
                            or False
                        )
                        full_access_authorized = bool(
                            mission.get("full_access_authorized_by_operator")
                            or mission.get("operator_authorized")
                        )
                        if session_mode == "full_access" and allow_unattended and full_access_authorized:
                            logger.warning(
                                "[SECURITY AUDIT] Auto-confirmando decisión %s en modo FULL_ACCESS_AUTONOMOUS...",
                                resp.decision_id,
                            )
                            try:
                                conf_res = self.confirm_decision(
                                    decision_id=resp.decision_id,
                                    approved=True,
                                    reason="Auto-aprobado por sesión Full Access Autónomo",
                                    operator_id="operator_full_access_auto",
                                    role="operator",
                                )
                                if conf_res.status == "ALLOW":
                                    exec_res = self.execute_decision(decision_id=resp.decision_id)
                                    executed_successfully = exec_res.success and not exec_res.is_error
                            except Exception as ex:
                                logger.warning("Error auto-confirmando en Full Access: %s", ex)
                        else:
                            wait_count = 0
                            while wait_count < 120 and not mission.get("stopped"):
                                time.sleep(0.5)
                                wait_count += 1
                                with self._lock:
                                    dec = self._decisions.get(resp.decision_id)
                                    if dec and dec.get("status") in ("ALLOW", "BLOCKED"):
                                        break
                            with self._lock:
                                dec = self._decisions.get(resp.decision_id)
                                dec_status = dec.get("status") if dec else None
                            if dec_status == "ALLOW":
                                try:
                                    exec_res = self.execute_decision(decision_id=resp.decision_id)
                                    executed_successfully = exec_res.success and not exec_res.is_error
                                except Exception as ex:
                                    logger.debug("Execution note: %s", ex)
                    elif resp.status == "ALLOW":
                        try:
                            exec_res = self.execute_decision(decision_id=resp.decision_id)
                            executed_successfully = exec_res.success and not exec_res.is_error
                        except Exception as ex:
                            logger.debug("Execution note: %s", ex)
                except Exception as err:
                    logger.error(f"Error proponiendo paso {step_idx} en sesión {sid}: {err}")
                    executed_successfully = False

                if step_data.get("simulate_failure"):
                    # Si el paso simulaba un fallo/veto para ilustrar poda y retroceso
                    executed_successfully = False
                    self.event_bus.emit(
                        session_id=sid,
                        event_type=EventType.DECISION_PRUNED,
                        node_id=action_node_id,
                        parent_id=target_parent,
                        payload={"reason": "Poda del supervisor: rama heurística no óptima."},
                    )
                    self.event_bus.emit(
                        session_id=sid,
                        event_type=EventType.INTERVENTION_APPLIED,
                        node_id=action_node_id,
                        parent_id=target_parent,
                        payload={
                            "intervention": "BACKTRACK_AND_BRANCH",
                            "message": f"Rama exploratoria '{action_node_id}' podada. Retrocediendo a '{target_parent}' para bifurcar hipótesis alternativa.",
                            "backtrack_to": target_parent,
                            "failed_node": action_node_id,
                        },
                    )

                if executed_successfully:
                    active_parent_id = action_node_id
                else:
                    # Retroceder al padre objetivo
                    active_parent_id = target_parent

                if step_data["tool"] == "finish":
                    break

                time.sleep(delay_sec)

        with self._lock:
            if sid in self._sessions_meta:
                self._sessions_meta[sid]["status"] = "Completed"

        final_summary = None
        with self._lock:
            if sid in self._sessions_meta:
                final_summary = self._sessions_meta[sid].get("final_answer")

        self.event_bus.emit(
            session_id=sid,
            event_type=EventType.SESSION_COMPLETED,
            node_id=f"root_{sid}",
            payload={"status": "completed", "summary": final_summary or f"Misión '{goal}' finalizada exitosamente."},
        )


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


import secrets
from fastapi import Header, HTTPException, Request, status


def verify_api_key(
    request: Request = None,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> Optional[str]:
    """Dependency de FastAPI para validar la presencia y autenticidad del API Key.
    
    Acepta 'X-API-Key' o 'Authorization: Bearer <key>'.
    En modo desarrollo sin claves configuradas emite advertencia de seguridad y permite el paso.
    En perfil de producción o con auth activa, deniega con HTTP 401 Unauthorized o 500 si falta configuración.
    """
    global _warned_dev_auth

    app_profile = getattr(request.app.state, "security_profile", None) if (request and hasattr(request, "app") and hasattr(request.app, "state")) else None
    client_host = request.client.host if (request and request.client) else None
    required = is_auth_required(profile=app_profile, client_host=client_host)
    expected_key = os.environ.get("PRAXEON_API_KEY") or os.environ.get("PRAXEON_SECRET_KEY")

    if not required:
        if not _warned_dev_auth:
            logger.warning("WARNING: PRAXEON running without API key authentication. Do not use in production.")
            _warned_dev_auth = True
        return None

    if not expected_key:
        logger.error("Fallo de seguridad evitado: Autenticación requerida pero no hay PRAXEON_API_KEY configurada.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error de configuración del servidor: Autenticación requerida pero no se ha establecido PRAXEON_API_KEY.",
        )

    # Extraer token de cabeceras
    token = x_api_key
    if not token and authorization:
        if authorization.startswith("Bearer "):
            token = authorization[7:].strip()
        else:
            token = authorization.strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida: proporcione 'X-API-Key' o 'Authorization: Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not secrets.compare_digest(token, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas: API key no coincide con la configurada en el servidor.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token

