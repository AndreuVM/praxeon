"""Pipeline formal de decisiones: propuesta, inspección, confirmación y ejecución."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


import hmac
import logging
import os
from typing import Any, Dict, List, Optional
import uuid

logger = logging.getLogger("praxeon.server.dependencies")

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
    ProviderAssessment,
    RiskLevel,
    ToolCall,
)
from praxeon.runtime.executor import PolicyViolation
from praxeon.runtime.state import SessionState, StepRecord
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



def get_active_security_profile():
    """Resuelve en tiempo de llamada el perfil activo (evita el import circular con dependencies)."""
    import praxeon.server.dependencies as deps
    return deps.get_active_security_profile()


def is_auth_required(*args, **kwargs):
    """Resuelve en tiempo de llamada la política de autenticación (respeta parches en tests)."""
    import praxeon.server.dependencies as deps
    return deps.is_auth_required(*args, **kwargs)


class DecisionPipelineMixin:
    """Pipeline formal de decisiones: propuesta, inspección, confirmación y ejecución."""

    # =========================================================================
    # PIPELINE DE DECISIÓN FORMAL (Proposal -> Evidence -> Risk -> Policy -> Capability)
    # =========================================================================

    def propose_action(
        self,
        session_id: str,
        proposal: ProposeActionRequest,
    ) -> DecisionResponse:
        """Punto de entrada de una propuesta externa pasando por todo el pipeline formal (delegado en StepExecutionCoordinator)."""
        return self.step_coordinator.propose_action(session_id, proposal)

    def _propose_action_impl(
        self,
        session_id: str,
        proposal: ProposeActionRequest,
    ) -> DecisionResponse:
        """Implementación interna del pipeline formal de propuestas."""
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

        # 4. Evaluación de Proveedores Semánticos aislada por sesión (LAYA / TypeSafe / Mock)
        session_provider = self.get_session_provider(session_id)
        assessments = session_provider.evaluate(state, [action])
        assessment = assessments[0] if assessments else ProviderAssessment(
            provider=getattr(session_provider, "name", self.config.provider.name),
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

        raw_name = getattr(session_provider, "name", None) or getattr(session_provider, "provider_name", None) or session_provider.__class__.__name__
        prov_name = str(raw_name).lower().replace("provider", "").replace("adapter", "").strip()
        if prov_name in ("mock", "replay"):
            provider_name_display = prov_name
        elif "laya" in prov_name:
            provider_name_display = "LAYA"
        elif "typesafe" in prov_name:
            provider_name_display = "TypeSafe"
        elif self.config.provider.name.lower() == "laya":
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
            decision_id=decision_id,
        )

        # Obtener configuración de autonomía de la sesión
        session_meta = {}
        with self._lock:
            if session_id in self._sessions_meta:
                session_meta = self._sessions_meta[session_id].get("metadata") or {}
        if not session_meta and state and state.metadata:
            session_meta = state.metadata

        # Mapear estado con precedencia determinista (BLOCK > REPLAN > REVIEW > ALLOW)
        requires_conf = decision.requires_confirmation
        preauthorized_reason = None
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
            step_confirmation = bool(session_meta.get("require_step_confirmation"))
            if mode_val == "full_access" and full_access_authorized and not step_confirmation:
                # FULL_ACCESS autorizado por el operador (doble confirmación al activarlo): no se pide confirmación por
                # paso. BLOCK, REPLAN y riesgo CRÍTICO se resolvieron antes y siguen siendo innegociables.
                requires_conf = False
                status_str = "ALLOW"
                policy_decision_str = "ALLOW"
                preauthorized_reason = "FULL_ACCESS_OPERATOR_PREAUTHORIZED"
                logger.warning(
                    "[SECURITY AUDIT] Sesión '%s' en FULL_ACCESS autorizado por el operador: confirmación por paso "
                    "omitida para la acción '%s' (motivos de revisión: %s).",
                    session_id,
                    action.tool_call.tool_name if action.tool_call else "cognitive",
                    ", ".join(decision.reason_codes),
                )
            else:
                # Otros modos, Full Access sin autorización o con confirmación por paso solicitada: confirmación interactiva
                status_str = "REVIEW"
                policy_decision_str = "REQUIRE_HUMAN_CONFIRMATION"

        else:
            status_str = decision.status.value.upper()
            policy_decision_str = status_str

        policy_dto = PolicyDTO(
            decision=policy_decision_str,
            reason_codes=list(decision.reason_codes) + ([preauthorized_reason] if preauthorized_reason else []),
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
        """Recupera los datos estructurados en las 4 pestañas requeridas por la Sección 6.3 (delegado en DiagnosticsCoordinator)."""
        return self.diagnostics_coordinator.get_decision_detail(decision_id)

    def _get_decision_detail_impl(self, decision_id: str) -> Optional[DecisionDetailResponse]:
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
        reason: Optional[str] = None,
        actor: str = "human_operator",
        operator_id: Optional[str] = None,
        role: str = "operator",
        operator_token: Optional[str] = None,
        caller_is_verified_operator: bool = False,
        security_profile: Optional[str] = None,
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
        capability_token: Optional[Dict[str, Any]] = None,
        operator_id: Optional[str] = None,
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
