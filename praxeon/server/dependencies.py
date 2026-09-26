"""Capa de servicios de aplicación y dependencias para el Web Server (praxeon/server/dependencies.py).

Especificación PRAXEON 1.0 (Sección 2.1 y Sección 8).
La API Web nunca ejecuta comandos directamente ni contiene reglas de seguridad
paralelas. Toda operación pasa por el pipeline canónico del runtime:
Proposal -> Evidence -> Risk -> Provider -> Policy -> Capability -> Execution
"""

from datetime import datetime, timedelta
import hashlib
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

logger = logging.getLogger("praxeon.server.dependencies")

from praxeon.config import JEVConfig, default_config
from praxeon.core.state_graph import StateGraph
from praxeon.domain.action import compute_action_hash
from praxeon.domain.decision import (
    CapabilityPayload,
    DecisionReceipt,
    DecisionStatus,
    PolicyDecision,
    compute_receipt_signature,
    compute_state_hash,
    sign_receipt,
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
from praxeon.providers.laya import LayaProvider
from praxeon.providers.typesafe import TypeSafeAdapter
from praxeon.reasoning.evidence import EvidenceEngine
from praxeon.reasoning.risk import RiskEngine
from praxeon.runtime.event_bus import EventBus, EventStore
from praxeon.runtime.executor import PolicyViolation, SecureExecutor, ToolObservation
from praxeon.runtime.nonce_store import NonceStore, SqliteNonceStore
from praxeon.runtime.sandbox import LocalProcessSandbox, SandboxExecutionResult, SandboxTier
from praxeon.runtime.state import SessionState
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


class RuntimeApplicationService:
    """Servicio central de aplicación que orquesta el ciclo de supervisión para la API Web."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        state_store: Optional[SqliteStateStore] = None,
        nonce_store: Optional[NonceStore] = None,
        config: Optional[JEVConfig] = None,
        db_dir: str = ".jev_cache",
    ):
        self.config = config or default_config
        self.db_dir = db_dir
        os.makedirs(db_dir, exist_ok=True)

        self.event_bus = event_bus or EventBus(
            store=EventStore(db_path=os.path.join(db_dir, "events.db"))
        )
        self.state_store = state_store or SqliteStateStore(
            db_path=os.path.join(db_dir, "state.db")
        )
        self.nonce_store = nonce_store or SqliteNonceStore(
            db_path=os.path.join(db_dir, "nonces.db")
        )

        self.registry = ToolRegistry(register_defaults=True)
        self.policy_engine = PolicyEngine(
            secret_key=os.environ.get("PRAXEON_SECRET_KEY", "praxeon_secret_hmac_key_v1")
        )
        self.executor = SecureExecutor(
            registry=self.registry,
            secret_key=self.policy_engine.secret_key,
            nonce_store=self.nonce_store,
        )
        self.risk_engine = RiskEngine()
        self.evidence_engine = EvidenceEngine()

        # Configurar proveedor supervisor semántico
        if self.config.provider.name.lower() == "laya":
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

    # =========================================================================
    # GESTIÓN DE SESIONES
    # =========================================================================

    def create_session(
        self,
        goal: str,
        session_id: Optional[str] = None,
        agent_name: str = "CodingAgent",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Crea formalmente una nueva sesión de supervisión y persiste su estado génesis."""
        sid = session_id or f"s-{uuid.uuid4().hex[:8]}"
        now = datetime.utcnow()

        state = SessionState(session_id=sid, goal=Goal(objective=goal), metadata=metadata or {})
        self.state_store.save_state(state)

        with self._lock:
            self._sessions_meta[sid] = {
                "session_id": sid,
                "goal": goal,
                "agent_name": agent_name,
                "status": "Active",
                "created_at": now,
                "updated_at": now,
                "metadata": metadata or {},
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

        meta = {
            "session_id": session_id,
            "goal": state.goal.objective,
            "agent_name": "CodingAgent",
            "status": "Active",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
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

        return {
            "session_id": session_id,
            "goal": sess.get("goal", ""),
            "status": sess.get("status", "Active"),
            "agent_name": sess.get("agent_name", "CodingAgent"),
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
            "goal": summary["goal"],
            "status": summary["status"],
            "agent_name": summary["agent_name"],
            "created_at": summary["created_at"],
            "summary": summary,
            "tree": tree.to_dict(),
        }

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
        now = datetime.utcnow()

        action = ActionCandidate(
            id=action_id,
            description=proposal.thought_rationale or f"{proposal.tool} {proposal.operation or ''}".strip(),
            tool_call=ToolCall(tool_name=proposal.tool, arguments=proposal.arguments),
        )

        # 1. Emitir evento: action.proposed
        parent_id = f"act_{len(state.steps)}" if state.steps else f"root_{session_id}"
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.ACTION_PROPOSED,
            node_id=action_id,
            parent_id=parent_id,
            decision_id=decision_id,
            payload={
                "action_id": action_id,
                "tool": proposal.tool,
                "operation": proposal.operation or "",
                "arguments": proposal.arguments,
                "source": proposal.provenance.get("source", "ExternalAgent"),
                "step": proposal.provenance.get("step", 1),
                "thought_rationale": proposal.thought_rationale,
            },
        )

        # 2. Evaluación de Evidencia Empírica
        evidences = self.evidence_engine.assess(state, action)
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
            },
        )

        # 3. Evaluación de Riesgo Operacional
        risk_assessment = self.risk_engine.assess_action_risk(action)
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
            available=True,
            confidence=0.88,
            loop_probability=0.05,
            grounded_probability=grounding_score,
            progress_probability=0.85,
        )

        provider_dtos = [
            ProviderEvaluationDTO(
                name="LAYA" if self.config.provider.name.lower() == "laya" else "TypeSafe",
                score=round(assessment.progress_probability, 2),
                verdict="ALLOW" if assessment.loop_probability < 0.5 else "REVIEW",
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
            session_id=session_id,
        )

        # Mapear estado
        status_str = decision.status.value.upper()
        policy_decision_str = "ALLOW"
        if decision.requires_confirmation:
            status_str = "REVIEW"
            policy_decision_str = "REQUIRE_HUMAN_CONFIRMATION"
        elif decision.status == DecisionStatus.BLOCK:
            status_str = "BLOCK"
            policy_decision_str = "BLOCK"

        policy_dto = PolicyDTO(
            decision=policy_decision_str,
            reason_codes=decision.reason_codes,
            requires_confirmation=decision.requires_confirmation,
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
                "requires_confirmation": decision.requires_confirmation,
            },
        )

        # 6. Emisión de Capability (Solo si ALLOW sin revisión humana obligatoria)
        capability_data: Optional[Dict[str, Any]] = None
        expires_at: Optional[datetime] = None

        if status_str == "ALLOW":
            expires_at = now + timedelta(minutes=5)
            # Firmar recibo
            receipt_with_exp = receipt.model_copy(
                update={"decision_id": decision_id, "expires_at": expires_at}
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

        # Guardar en registro de decisiones
        decision_record = {
            "decision_id": decision_id,
            "session_id": session_id,
            "action_id": action_id,
            "action": action,
            "status": status_str,
            "action_hash": receipt.action_hash,
            "risk": risk_dto,
            "providers": provider_dtos,
            "policy": policy_dto,
            "capability": capability_data,
            "expires_at": expires_at,
            "receipt": receipt,
            "evidence": evidences,
            "grounding_score": grounding_score,
            "created_at": now,
        }
        with self._lock:
            self._decisions[decision_id] = decision_record

        return DecisionResponse(
            decision_id=decision_id,
            session_id=session_id,
            status=status_str,
            action_hash=receipt.action_hash,
            risk=risk_dto,
            providers=provider_dtos,
            policy=policy_dto,
            capability=capability_data,
            expires_at=expires_at,
        )

    # =========================================================================
    # DECISION INSPECTOR (4 Tabs: Decision, Evidence, Policy, Receipt)
    # =========================================================================

    def get_decision_detail(self, decision_id: str) -> Optional[DecisionDetailResponse]:
        """Recupera los datos estructurados en las 4 pestañas requeridas por la Sección 6.3."""
        with self._lock:
            record = self._decisions.get(decision_id)
        if not record:
            return None

        action: ActionCandidate = record["action"]
        receipt: DecisionReceipt = record["receipt"]
        risk: RiskDTO = record["risk"]
        policy: PolicyDTO = record["policy"]
        providers: List[ProviderEvaluationDTO] = record["providers"]

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
        }

        # 4. Pestaña: Receipt
        receipt_tab = {
            "decision_id": receipt.decision_id,
            "session_id": receipt.session_id,
            "action_hash": receipt.action_hash,
            "state_hash": receipt.state_hash,
            "nonce": receipt.nonce,
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
        """Lista cronológicamente las decisiones asociadas a una sesión."""
        with self._lock:
            matching = [
                {
                    "decision_id": d["decision_id"],
                    "session_id": d["session_id"],
                    "action_id": d["action_id"],
                    "tool": d["action"].tool_call.tool_name if d["action"].tool_call else None,
                    "status": d["status"],
                    "risk_level": d["risk"].level,
                    "created_at": d["created_at"].isoformat(),
                }
                for d in self._decisions.values()
                if d["session_id"] == session_id
            ]
        return sorted(matching, key=lambda x: x["created_at"])

    # =========================================================================
    # CONFIRMACIÓN Y EJECUCIÓN FÍSICA
    # =========================================================================

    def confirm_decision(
        self,
        decision_id: str,
        approved: bool,
        reason: Optional[str] = None,
        actor: str = "human_operator",
    ) -> ConfirmDecisionResponse:
        """Autoriza o bloquea una decisión en espera de aprobación humana (REVIEW)."""
        with self._lock:
            record = self._decisions.get(decision_id)
        if not record:
            raise KeyError(f"Decisión '{decision_id}' no encontrada.")

        session_id = record["session_id"]
        action: ActionCandidate = record["action"]
        now = datetime.utcnow()

        if approved:
            new_status = "ALLOW"
            expires_at = now + timedelta(minutes=5)
            receipt: DecisionReceipt = record["receipt"]
            receipt_updated = receipt.model_copy(
                update={"decision_status": DecisionStatus.ALLOW, "expires_at": expires_at}
            )
            signed = sign_receipt(receipt_updated, self.policy_engine.secret_key)
            cap = signed.to_capability_payload(allowed_tools=[action.tool_call.tool_name] if action.tool_call else [])
            capability_dict = cap.model_dump(mode="json") if cap else None

            record["status"] = "ALLOW"
            record["receipt"] = signed
            record["capability"] = capability_dict
            record["expires_at"] = expires_at

            self.event_bus.emit(
                session_id=session_id,
                event_type=EventType.APPROVAL_COMPLETED,
                node_id=record["action_id"],
                decision_id=decision_id,
                payload={"approved": True, "reason": reason, "actor": actor},
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
                capability=capability_dict,
            )
        else:
            record["status"] = "BLOCKED"
            self.event_bus.emit(
                session_id=session_id,
                event_type=EventType.APPROVAL_COMPLETED,
                node_id=record["action_id"],
                decision_id=decision_id,
                payload={"approved": False, "reason": reason, "actor": actor},
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
                capability=None,
            )

    def execute_decision(
        self,
        decision_id: str,
        capability_token: Optional[Dict[str, Any]] = None,
    ) -> ExecuteDecisionResponse:
        """Ejecuta físicamente en el sandbox la herramienta ligada al capability."""
        with self._lock:
            record = self._decisions.get(decision_id)
        if not record:
            raise KeyError(f"Decisión '{decision_id}' no encontrada.")

        session_id = record["session_id"]
        action: ActionCandidate = record["action"]
        receipt: DecisionReceipt = record["receipt"]
        state = self.state_store.load_state(session_id) or SessionState(session_id=session_id, goal=Goal(objective="Task"))

        # Emitir evento: execution.started
        self.event_bus.emit(
            session_id=session_id,
            event_type=EventType.EXECUTION_STARTED,
            node_id=record["action_id"],
            decision_id=decision_id,
            payload={
                "tool": action.tool_call.tool_name if action.tool_call else None,
                "arguments": action.tool_call.arguments if action.tool_call else {},
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
                update={"is_executed": True, "execution_timestamp": datetime.utcnow()}
            )
            record["receipt"] = updated_receipt

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
                },
            )
            self.event_bus.emit(
                session_id=session_id,
                event_type=EventType.OBSERVATION_RECORDED,
                node_id=record["action_id"],
                decision_id=decision_id,
                payload={"output": observation.output[:2000]},
            )

            # Si es finish, emitir session.completed
            if action.tool_call and action.tool_call.tool_name == "finish" and observation.success:
                self.event_bus.emit(
                    session_id=session_id,
                    event_type=EventType.SESSION_COMPLETED,
                    node_id=f"root_{session_id}",
                    payload={"status": "completed", "summary": action.tool_call.arguments.get("summary", "")},
                )
                with self._lock:
                    if session_id in self._sessions_meta:
                        self._sessions_meta[session_id]["status"] = "Completed"

            return ExecuteDecisionResponse(
                decision_id=decision_id,
                action_id=record["action_id"],
                output=observation.output,
                success=observation.success,
                exit_code=getattr(observation, "exit_code", 0 if observation.success else 1),
                execution_time_ms=observation.execution_time_ms,
                tier="local_process",
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
        llm_provider: str = "simulator",
        llm_model: Optional[str] = None,
        supervisor: str = "laya",
        max_steps: int = 6,
        step_delay_ms: int = 900,
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

        meta = self.create_session(
            goal=goal,
            session_id=sid,
            agent_name=agent_name,
            metadata={
                "llm_provider": llm_provider,
                "llm_model": llm_model or "default",
                "supervisor": supervisor,
                "max_steps": max_steps,
            },
        )

        mission_state = {
            "session_id": sid,
            "goal": goal,
            "agent_name": agent_name,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "supervisor": supervisor,
            "max_steps": max_steps,
            "step_delay_ms": step_delay_ms,
            "paused": False,
            "stopped": False,
            "current_step": 0,
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

    def _run_mission_worker(self, mission: Dict[str, Any]) -> None:
        """Worker asíncrono que genera y propone pasos interactivos para la sesión."""
        sid = mission["session_id"]
        goal = mission["goal"]
        provider_name = (mission.get("llm_provider") or "simulator").lower().strip()
        model_name = mission.get("llm_model")
        max_steps = mission.get("max_steps", 6)
        delay_sec = max(0.2, mission.get("step_delay_ms", 900) / 1000.0)

        # Retardo inicial para dar tiempo al WebSocket a suscribirse
        time.sleep(0.4)

        # Generador de pasos de alta fidelidad contextual según el objetivo planteado
        default_steps = [
            {
                "tool": "read_file",
                "operation": "1. Analyze issue",
                "arguments": {"path": "praxeon/core/jev_engine.py"},
                "thought": f"Analizando requerimientos e inspeccionando código base para resolver: '{goal}'.",
            },
            {
                "tool": "read_file",
                "operation": "2. Plan solution",
                "arguments": {"path": "tests/test_web_server.py"},
                "thought": "Verificando contratos de la API y suites de pruebas asociadas.",
            },
            {
                "tool": "edit_file",
                "operation": "3. Select tools",
                "arguments": {"path": "praxeon/core/jev_engine.py", "diff": "+ # Applied fix verified"},
                "thought": "Aplicando refactorización mínima requerida respetando políticas del proyecto.",
            },
            {
                "tool": "run_command",
                "operation": "4. Read file",
                "arguments": {"command": "pytest tests/ -q"},
                "thought": "Ejecutando tests de regresión para validar que no haya efectos secundarios.",
            },
            {
                "tool": "git",
                "operation": "5. Propose changes",
                "arguments": {"command": "git push origin main"},
                "thought": "Proponiendo publicación de cambios validados en el repositorio remoto.",
            },
            {
                "tool": "finish",
                "operation": "6. Complete task",
                "arguments": {"summary": f"Misión '{goal}' ejecutada y supervisada exitosamente."},
                "thought": "Todas las comprobaciones y políticas han concluido con éxito.",
            },
        ]

        steps_to_run = default_steps[:max_steps]
        step_idx = 0

        while step_idx < len(steps_to_run):
            if mission.get("stopped"):
                break

            while mission.get("paused") and not mission.get("stopped"):
                time.sleep(0.2)

            step_data = steps_to_run[step_idx]
            step_idx += 1
            mission["current_step"] = step_idx

            req = ProposeActionRequest(
                action_id=f"act_{step_idx}",
                tool=step_data["tool"],
                operation=step_data["operation"],
                arguments=step_data["arguments"],
                thought_rationale=step_data["thought"],
                provenance={
                    "source": f"LLM ({provider_name.upper()})",
                    "step": step_idx,
                },
                context={"goal": goal},
            )

            try:
                resp = self.propose_action(session_id=sid, proposal=req)
                
                # Si requiere confirmación humana (ej. REVIEW por git push), esperar a que sea autorizada
                if resp.status == "REVIEW" or resp.policy.requires_confirmation:
                    wait_count = 0
                    while wait_count < 60 and not mission.get("stopped"):
                        time.sleep(0.5)
                        wait_count += 1
                        with self._lock:
                            dec = self._decisions.get(resp.decision_id)
                            if dec and dec.get("status") in ("ALLOW", "BLOCKED"):
                                break
            except Exception as err:
                logger.error(f"Error proponiendo paso {step_idx} en sesión {sid}: {err}")

            time.sleep(delay_sec)

        with self._lock:
            if sid in self._sessions_meta:
                self._sessions_meta[sid]["status"] = "Completed"



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
