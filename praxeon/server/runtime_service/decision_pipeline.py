"""Pipeline formal de decisiones: propuesta, inspección, confirmación y ejecución."""

from __future__ import annotations

from datetime import datetime, timedelta, UTC


import hmac
import logging
import os
from praxeon.policy.sanitizer import payload_limit_from_env
import re
from typing import Any

logger = logging.getLogger("praxeon.server.dependencies")

from praxeon.domain.assessment import CommandCategory
from praxeon.domain.decision import (
    DecisionReceipt,
    DecisionStatus,
    ExecutionMode,
    PolicyDecision,
    sign_receipt,
    verify_capability_signature,
    verify_receipt_signature,
)
from praxeon.domain.events import EventType
from praxeon.domain.models import (
    ActionCandidate,
    Goal,
)
from praxeon.runtime.executor import PolicyViolation
from praxeon.runtime.state import SessionState, StepRecord
from praxeon.server.schemas.action import ProposeActionRequest
from praxeon.server.runtime_service.proposal_pipeline import run_proposal
from praxeon.server.schemas.decision import (
    ConfirmDecisionResponse,
    DecisionDetailResponse,
    DecisionResponse,
    ExecuteDecisionResponse,
    PolicyDTO,
    ProviderEvaluationDTO,
    RiskDTO,
)



def is_auth_required(*args, **kwargs):
    """Resuelve en tiempo de llamada la política de autenticación (evita el import circular con el servidor)."""
    from praxeon.server import auth_deps
    return auth_deps.is_auth_required(*args, **kwargs)


class DecisionPipelineMixin:
    """Pipeline formal de decisiones: propuesta, inspección, confirmación y ejecución."""

    # =========================================================================
    # PIPELINE DE DECISIÓN FORMAL (Proposal -> Evidence -> Risk -> Policy -> Capability)
    # =========================================================================

    def _may_auto_confirm(self, tool: str | None) -> bool:
        """Full Access autorizado aprueba solo lo que pide confirmación; las herramientas externas con efectos (MCP) no."""
        registry = getattr(self, "registry", None)
        spec = registry.get_tool(tool) if registry is not None and tool else None
        return spec is None or spec.auto_confirm

    def _adopt_external_tool_profile(self, tool: str | None, assessment: Any) -> Any:
        """Una herramienta externa registrada (MCP) que el clasificador no reconoce (UNKNOWN) se clasifica según su ToolSpec.

        Solo se rebaja a «inspección» lo que el registro marca como solo lectura (por decisión del operador); cualquier
        veto del clasificador (destructivo, privilegios…) se respeta, y lo demás sigue pidiendo confirmación.
        """
        spec = self.registry.get_tool(tool) if tool else None
        if spec is None or not ((spec.source or "").startswith("mcp:") or spec.source == "web") or assessment.category != CommandCategory.UNKNOWN:
            return assessment
        if not spec.read_only and not spec.requires_confirmation:
            # El operador desactivó expresamente la confirmación de esta herramienta: sigue bajo el supervisor y la
            # política (riesgos HIGH/CRITICAL fuerzan la confirmación al registrarla), pero no se detiene a pedirla.
            return assessment.model_copy(update={
                "category": CommandCategory.LOCAL_MUTATION,
                "risk_level": spec.risk_level,
                "read_only": False,
                "reversible": False,
                "external_side_effect": True,
                "reasons": [f"Herramienta externa ({spec.source}) sin confirmación por decisión del operador."],
                "matched_rules": [*assessment.matched_rules, "EXTERNAL_TOOL_OPERATOR_TRUSTED"],
            })
        if not spec.read_only:
            return assessment.model_copy(update={"reasons": [*assessment.reasons, f"Herramienta externa ({spec.source}) con efectos: requiere confirmación."]})
        return assessment.model_copy(update={
            "category": CommandCategory.INSPECTION,
            "risk_level": spec.risk_level,
            "read_only": True,
            "reversible": True,
            "external_side_effect": False,
            "reasons": [f"Herramienta externa ({spec.source}) de solo lectura según su registro."],
            "matched_rules": [*assessment.matched_rules, "EXTERNAL_TOOL_READ_ONLY"],
        })

    def propose_action(
        self,
        session_id: str,
        proposal: ProposeActionRequest,
    ) -> DecisionResponse:
        """Punto de entrada de una propuesta externa pasando por todo el pipeline formal (delegado en StepExecutionCoordinator)."""
        return self.step_coordinator.propose_action(session_id, proposal)

    @staticmethod
    def _node_scoped_state(state: SessionState, session_meta: dict[str, Any], action_id: str) -> SessionState:
        """Vista del estado con solo los pasos del nodo de workflow que propone la acción (el resto de sesiones, intacto)."""
        if not session_meta.get("workflow_id"):
            return state
        m = re.match(r"^(?P<prefix>.+_i\d+_)act_\d+$", action_id or "")
        if not m:
            return state
        prefix = m.group("prefix")
        own = [st for st in state.steps if str(getattr(st, "id", "")).startswith(prefix)]
        return state.model_copy(update={"steps": own})

    def _propose_action_impl(
        self,
        session_id: str,
        proposal: ProposeActionRequest,
    ) -> DecisionResponse:
        """Pipeline formal de propuestas: los pasos y su orden están en ``proposal_pipeline.PROPOSAL_STEPS``."""
        return run_proposal(self, session_id, proposal)

    # =========================================================================
    # DECISION INSPECTOR (4 Tabs: Decision, Evidence, Policy, Receipt)
    # =========================================================================

    def get_decision_detail(self, decision_id: str) -> DecisionDetailResponse | None:
        """Recupera los datos estructurados en las 4 pestañas requeridas por la Sección 6.3 (delegado en DiagnosticsCoordinator)."""
        return self.diagnostics_coordinator.get_decision_detail(decision_id)

    def _get_decision_detail_impl(self, decision_id: str) -> DecisionDetailResponse | None:
        """Implementación interna de recuperación de detalle de decisión."""
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
        providers: list[ProviderEvaluationDTO] = record["providers"]

        # Determinar atributos de ejecución para la subsección Execution
        mode_val = record.get("execution_mode") or getattr(receipt, "execution_mode", "local_restricted")
        if hasattr(mode_val, "value"):
            mode_val = mode_val.value
        state = self.state_store.load_state(record["session_id"])
        working_dir = (state.metadata.get("working_directory") if state and state.metadata else None) or os.getcwd()
        backend_name = (
            "FullAccessExecutor" if mode_val == "full_access"
            else ("IsolatedSandbox" if mode_val == "container" else "LocalProcessSandbox")
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

    def list_decisions_for_session(self, session_id: str) -> list[dict[str, Any]]:
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

    def list_all_decisions(self, session_id: str | None = None, limit: int = 150) -> list[dict[str, Any]]:
        """Lista cronológicamente o por recencia las decisiones del sistema."""
        if session_id:
            return self.list_decisions_for_session(session_id)

        records = self.decision_repository.list_all(limit=limit)
        with self._lock:
            for d in self._decisions.values():
                if not any(r.get("decision_id") == d.get("decision_id") for r in records):
                    records.append(d)

        return self._format_decision_records(records)

    def _format_decision_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
            elif tool_name in ("search_web", "web_search"):
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
                created_at_str = str(created_at_val or datetime.now(UTC).isoformat())

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
        reason: str | None = None,
        actor: str = "human_operator",
        operator_id: str | None = None,
        role: str = "operator",
        operator_token: str | None = None,
        caller_is_verified_operator: bool = False,
        security_profile: str | None = None,
    ) -> ConfirmDecisionResponse:
        """Autoriza o bloquea una decisión en espera de aprobación humana (delegado en StepExecutionCoordinator)."""
        return self.step_coordinator.confirm_decision(
            decision_id=decision_id,
            approved=approved,
            reason=reason,
            actor=actor,
            operator_id=operator_id,
            role=role,
            operator_token=operator_token,
            caller_is_verified_operator=caller_is_verified_operator,
            security_profile=security_profile,
        )

    def _confirm_decision_impl(
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
    ) -> ConfirmDecisionResponse:
        """Implementación interna de autorización o bloqueo de decisión."""
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
            or os.getenv("PRAXEON_API_KEY")
        )
        is_verified_operator = caller_is_verified_operator
        if operator_token:
            # Cualquiera de las claves configuradas vale: la de operador, la de firma o la maestra de API (la que usa la UI;
            # en modo escritorio la de firma es otra, efímera).
            candidates = (os.getenv("PRAXEON_OPERATOR_KEY"), os.getenv("PRAXEON_API_KEY"))
            if any(c and hmac.compare_digest(str(operator_token).strip(), str(c).strip()) for c in candidates):
                is_verified_operator = True

        effective_profile = security_profile or getattr(self, "security_profile", None)
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
        now = datetime.now(UTC)
        session_mode = record.get("execution_mode") or "local_restricted"

        if approved:
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
        operator_id: str | None = None,
        role: str = "operator",
        operator_token: str | None = None,
        caller_is_verified_operator: bool = False,
        security_profile: str | None = None,
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
        capability_token: dict[str, Any] | None = None,
        operator_id: str | None = None,
        role: str = "operator",
    ) -> ExecuteDecisionResponse:
        """Ejecuta físicamente la herramienta autorizada en el sandbox o host (delegado en StepExecutionCoordinator)."""
        return self.step_coordinator.execute_decision(
            decision_id=decision_id,
            capability_token=capability_token,
            operator_id=operator_id,
            role=role,
        )

    def _execute_decision_impl(
        self,
        decision_id: str,
        capability_token: dict[str, Any] | None = None,
        operator_id: str | None = None,
        role: str = "operator",
    ) -> ExecuteDecisionResponse:
        """Implementación interna de ejecución de decisión."""
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
                        now_utc = datetime.now(UTC)
                        if exp_dt.tzinfo is None:
                            exp_dt = exp_dt.replace(tzinfo=UTC)
                        if now_utc > exp_dt:
                            raise PolicyViolation(f"Ejecución física DENEGADA: El capability token ha expirado (expiró en: {exp_dt.isoformat()}).")
                except PolicyViolation:
                    raise
                except (TypeError, ValueError) as exc:
                    # Caducidad ilegible: se rechaza (falla cerrado), no se ejecuta sin comprobarla
                    raise PolicyViolation("Ejecución física DENEGADA: caducidad del capability token ilegible.") from exc

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
                update={"is_executed": True, "execution_timestamp": datetime.now(UTC)}
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
                payload={"output": observation.output[:payload_limit_from_env()]},
            )

            # Si es finish o herramienta de conclusión de ciclo de vida
            if action.tool_call and action.tool_call.tool_name in ("finish", "complete_task", "done", "complete", "task_completed") and observation.success:
                summary = (
                    action.tool_call.arguments.get("summary")
                    or action.tool_call.arguments.get("final_answer")
                    or observation.output.replace("Tarea concluida: ", "")
                    or "Misión finalizada exitosamente."
                )
                with self._lock:
                    in_workflow = bool(((self._sessions_meta.get(session_id) or {}).get("metadata") or {}).get("workflow_id"))
                # En una misión con workflow, el finish de un agente solo cierra SU nodo (hito); la misión
                # termina cuando el grafo llega al final (lo emite el runtime de la misión).
                if not in_workflow:
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
