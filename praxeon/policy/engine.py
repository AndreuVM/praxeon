"""Motor de políticas operacionales (PolicyEngine) para JEV Reasoning Navigator v0.2.

Aplica la separación estricta:
Semantic Judgment (JEV) != Operational Policy (PolicyEngine) != Physical Execution (Executor)
"""

from datetime import datetime, timedelta, timezone
import secrets
import time

from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from praxeon.domain.models import (
    ActionCandidate,
    CommandCategory,
    CommandRiskAssessment,
    DecisionReceipt,
    DecisionStatus,
    Evidence,
    PolicyDecision,
    ProviderAssessment,
    RiskAssessment,
    RiskLevel,
    compute_action_hash,
    compute_receipt_signature,
    compute_state_hash,
)
from praxeon.policy.failsafe import FailSafePolicy
from praxeon.policy.permissions import PermissionManager
from praxeon.policy.reconciliation import RiskReconciler
from praxeon.policy.registry import ToolRegistry
from praxeon.reasoning.classifier import CommandClassifier


# Reglas del clasificador que no se vetan pero exigen que una persona confirme (se pierde contenido sin copia)
REVIEW_RULES = frozenset({"RULE_WORKSPACE_DELETE_REVIEW", "RULE_OVERWRITE_EXISTING_FILE"})

# Herramientas de solo lectura aunque el clasificador no se pronuncie
READ_ONLY_TOOLS = frozenset({"read_file", "view_file", "list_dir", "grep_search", "search_web", "web_search", "web_fetch", "read_url_content"})


class PolicyEngine:
    """Combina señales semánticas, evidencia empírica, riesgo y fail-safe para emitir decisiones operacionales."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        failsafe: Optional[FailSafePolicy] = None,
        permission_manager: Optional[PermissionManager] = None,
        loop_threshold: float = 0.65,
        min_grounded_threshold: float = 0.35,
        min_confidence_threshold: float = 0.30,
        veto_min_confidence: float = 0.50,
        advisory_review_min_probability: float = 0.40,
        secret_key: Optional[str] = None,
        receipt_ttl_seconds: float = 60.0,
        classifier: Optional[CommandClassifier] = None,
        reconciler: Optional[RiskReconciler] = None,
    ):
        self.registry = registry or ToolRegistry(register_defaults=True)
        self.failsafe = failsafe or FailSafePolicy()
        self.permission_manager = permission_manager or PermissionManager(registry=self.registry)
        self.classifier = classifier or CommandClassifier()
        self.reconciler = reconciler or RiskReconciler(registry=self.registry)
        self.loop_threshold = loop_threshold
        self.min_grounded_threshold = min_grounded_threshold
        self.min_confidence_threshold = min_confidence_threshold
        self.veto_min_confidence = veto_min_confidence
        self.advisory_review_min_probability = advisory_review_min_probability
        self.secret_key = secret_key or secrets.token_hex(32)
        self.receipt_ttl_seconds = receipt_ttl_seconds

    # ------------------------------------------------------------------ etapas de evaluate_action
    # evaluate_action orquesta; cada etapa es una función pequeña y comprobable por separado (auditoría A-01).

    @staticmethod
    def _resolve_execution_mode(state: Any, execution_mode: Optional[str]) -> str:
        if execution_mode is not None:
            return execution_mode
        if isinstance(state, dict):
            return state.get("metadata", {}).get("execution_mode", "local_restricted")
        if hasattr(state, "metadata") and isinstance(state.metadata, dict):
            return state.metadata.get("execution_mode", "local_restricted")
        return "local_restricted"

    def _classify_operation(self, action: ActionCandidate, state: Any) -> CommandRiskAssessment:
        """Clasifica la operación concreta de la acción cuando nadie la clasificó antes."""
        op_text = ""
        if action.tool_call:
            args = action.tool_call.arguments or {}
            cmd = str(args.get("command") or args.get("cmd") or args.get("raw") or "").strip()
            if not cmd:
                op_text = action.tool_call.tool_name
            elif action.tool_call.tool_name != "run_command":
                op_text = f"{action.tool_call.tool_name} {cmd}".strip()
            else:
                op_text = cmd
        else:
            op_text = action.description or ""
        return self.classifier.classify(op_text, context=state if isinstance(state, dict) else None)

    @staticmethod
    def _is_read_only(operation_assessment: Optional[CommandRiskAssessment], spec: Any, tool_name: Optional[str]) -> bool:
        return bool(
            (operation_assessment.read_only if operation_assessment else False)
            or (spec.read_only if spec else False)
            or (tool_name in READ_ONLY_TOOLS)
        )

    def _screen_action(
        self,
        action: ActionCandidate,
        tool_name: Optional[str],
        forbidden_tools: Set[str],
        available_evidence: List[Evidence],
        completion_assessment: Optional[Any],
        provider_assessment: Optional[ProviderAssessment],
        operation_assessment: Optional[CommandRiskAssessment],
        risk: RiskAssessment,
        is_read_only: bool,
        reason_codes: List[str],
    ) -> Optional[DecisionStatus]:
        """Pasos 1 a 5: herramientas prohibidas, barreras críticas, evidencia, completitud y supervisor.

        Devuelve el primer veredicto que aplica (el orden es la prioridad) o None si ninguna etapa decide.
        """
        # 1. Enforcement de herramientas prohibidas en el estado actual (Poda / Backtracking)
        if tool_name and tool_name in forbidden_tools:
            reason_codes.append("TOOL_FORBIDDEN_BY_SUPERVISOR")
            return DecisionStatus.BLOCK

        # 1.5. Señal de incertidumbre para herramientas no registradas (CHG-01: Unknown != Malicious)
        if tool_name and not self.registry.is_known(tool_name):
            reason_codes.append("UNKNOWN_TOOL_UNCERTAIN")
            reason_codes.append("UNKNOWN_TOOL_NOT_REGISTERED")

        # 2. Enforcement determinista de barreras críticas (PRIVILEGE y DESTRUCTIVE)
        if operation_assessment is not None and (
            operation_assessment.category == CommandCategory.PRIVILEGE or operation_assessment.privilege_escalation
        ):
            reason_codes.append("PRIVILEGE_ESCALATION_BLOCK")
            reason_codes.append("CRITICAL_OPERATIONAL_RISK")
            return DecisionStatus.BLOCK

        if operation_assessment is not None and (
            operation_assessment.category == CommandCategory.DESTRUCTIVE or operation_assessment.destructive
        ):
            reason_codes.append("DESTRUCTIVE_COMMAND_BLOCK")
            reason_codes.append("CRITICAL_OPERATIONAL_RISK")
            if provider_assessment is not None and not provider_assessment.available:
                reason_codes.append("PROVIDER_UNAVAILABLE_DESTRUCTIVE_BLOCK")
            return DecisionStatus.BLOCK

        # 3. Verificación formal de evidencia requerida (Groundedness estricto)
        evidence_claims = {ev.claim.lower().strip() for ev in available_evidence}
        if action.requires_evidence and any(req.lower().strip() not in evidence_claims for req in action.requires_evidence):
            missing_evidence = [req for req in action.requires_evidence if req.lower().strip() not in evidence_claims]
            reason_codes.append(f"MISSING_REQUIRED_EVIDENCE: {', '.join(missing_evidence)}")
            return DecisionStatus.REPLAN

        # 3.5. Verificación formal de completitud ante intentos de finish
        if completion_assessment is not None and not getattr(completion_assessment, "is_complete", True):
            reasons = (
                getattr(completion_assessment, "missing_criteria", [])
                or getattr(completion_assessment, "unverified_claims", [])
                or [getattr(completion_assessment, "rationale", "")]
            )
            reason_codes.append(f"UNVERIFIED_COMPLETION: {', '.join(reasons)}")
            return DecisionStatus.REPLAN

        if provider_assessment is None:
            return None

        # 4. Evaluación de disponibilidad del proveedor (Fail-safe explícito - Hallazgo 3.2)
        if not provider_assessment.available:
            status = self.failsafe.resolve_provider_failure(risk, is_read_only)
            if status == DecisionStatus.BLOCK:
                reason_codes.append("PROVIDER_UNAVAILABLE_DESTRUCTIVE_BLOCK")
            elif status == DecisionStatus.ALLOW:
                reason_codes.append("PROVIDER_UNAVAILABLE_READ_ONLY_ALLOWED")
            else:
                reason_codes.append("PROVIDER_UNAVAILABLE_FAILSAFE_ABSTAIN")
            return status

        # 5. Evaluación semántica probabilística de JEV / LAYA
        return self._supervisor_verdict(provider_assessment, reason_codes)

    def _supervisor_verdict(self, provider_assessment: ProviderAssessment, reason_codes: List[str]) -> Optional[DecisionStatus]:
        """Paso 5: traduce el juicio del supervisor semántico en un veredicto (o ninguno)."""
        metadata = provider_assessment.metadata if isinstance(provider_assessment.metadata, dict) else {}
        choice_meta = metadata.get("choice", {})
        vetoed_label = choice_meta.get("label")
        # solo proveedores cuya confianza es la probabilidad de la opción elegida (LAYA, KEV); la de TypeSafe está normalizada
        weak_veto = (
            str(getattr(provider_assessment, "provider", "")).lower() in ("laya", "kev")
            and vetoed_label in ("BLOCK", "REPLAN")
            and provider_assessment.confidence is not None
            and provider_assessment.confidence < self.veto_min_confidence
        )
        # Supervisor consultivo (p. ej. LAYA sin ajuste fino): su etiqueta es una señal de aviso, no un veto. Un BLOCK
        # probable pide revisión humana, un REPLAN se ignora, y su confianza no sirve de umbral de escalado.
        provider_tag = str(getattr(provider_assessment, "provider", "") or "supervisor").upper()
        advisory = metadata.get("veto_mode") == "advisory"
        probs = choice_meta.get("raw_probabilities") or choice_meta.get("probabilities") or {}
        advisory_review = bool(advisory and vetoed_label == "BLOCK" and float(probs.get("BLOCK", 0.0)) >= self.advisory_review_min_probability)
        if advisory and vetoed_label in ("BLOCK", "REPLAN") and not advisory_review:
            reason_codes.append(f"ADVISORY_{vetoed_label}_IGNORED")
        if weak_veto and not advisory:
            reason_codes.append(
                f"WEAK_{vetoed_label}_VETO_DEFERRED ({provider_assessment.confidence:.2f} < {self.veto_min_confidence:.2f})"
            )

        # Gating de confianza según JEV-as-a-Judge: juicios con baja confianza escalan a ABSTAIN
        if advisory_review:
            reason_codes.append(
                f"SUPERVISOR_{provider_tag}_ADVISORY_REVIEW (p_block={float(probs.get('BLOCK', 0.0)):.2f} >= {self.advisory_review_min_probability:.2f})"
            )
            return DecisionStatus.ABSTAIN
        if (
            not advisory
            and provider_assessment.confidence is not None
            and provider_assessment.confidence < self.min_confidence_threshold
        ):
            reason_codes.append(
                f"LOW_PROVIDER_CONFIDENCE_ESCALATE ({provider_assessment.confidence:.2f} < {self.min_confidence_threshold:.2f})"
            )
            return DecisionStatus.ABSTAIN

        # Clasificación de acción destructiva o peligrosa por LAYA. Un veto con probabilidad calibrada baja es ruido
        # de un modelo que duda: no veta por sí solo, decide el análisis determinista de riesgo (paso 6).
        if not advisory and not weak_veto and (
            "LAYA_DESTRUCTIVE_BLOCK" in provider_assessment.reason_codes or vetoed_label == "BLOCK"
        ):
            reason_codes.append("SUPERVISOR_LAYA_DESTRUCTIVE_BLOCK")
            return DecisionStatus.BLOCK

        # Detección de bucle o degradación cíclica
        if provider_assessment.loop_probability is not None and provider_assessment.loop_probability >= self.loop_threshold:
            reason_codes.append(f"HIGH_LOOP_PROBABILITY ({provider_assessment.loop_probability:.2f} >= {self.loop_threshold})")
            return DecisionStatus.REPLAN

        # Clasificación de acción innecesaria o desvío por LAYA
        if not advisory and not weak_veto and (
            "LAYA_UNNECESSARY_ACTION_REPLAN" in provider_assessment.reason_codes or vetoed_label == "REPLAN"
        ):
            reason_codes.append("SUPERVISOR_LAYA_UNNECESSARY_ACTION_REPLAN")
            return DecisionStatus.REPLAN

        # Detección de premisa no fundamentada o alucinación semántica. Un supervisor consultivo solo avisa: si vetara
        # por esto, bloquearía hasta las lecturas con las que el agente reúne la evidencia que se le exige.
        if (
            not advisory
            and provider_assessment.grounded_probability is not None
            and provider_assessment.grounded_probability < self.min_grounded_threshold
        ):
            reason_codes.append(
                f"LOW_GROUNDED_PROBABILITY ({provider_assessment.grounded_probability:.2f} < {self.min_grounded_threshold})"
            )
            for rc in provider_assessment.reason_codes or []:
                if rc not in reason_codes:
                    reason_codes.append(rc)
            return DecisionStatus.REPLAN
        return None

    def _confirm_or_abstain(
        self, action: ActionCandidate, action_hash: str, abstain_code: str, reason_codes: List[str]
    ) -> DecisionStatus:
        """ALLOW si una persona ya confirmó esta acción exacta; si no, ABSTAIN con el motivo dado."""
        if self.permission_manager.is_action_confirmed(action.id, action_hash=action_hash):
            reason_codes.append("HUMAN_CONFIRMED_ACTION")
            return DecisionStatus.ALLOW
        reason_codes.append(abstain_code)
        return DecisionStatus.ABSTAIN

    def _assess_operational_risk(
        self,
        action: ActionCandidate,
        action_hash: str,
        tool_name: Optional[str],
        operation_assessment: Optional[CommandRiskAssessment],
        risk: RiskAssessment,
        reason_codes: List[str],
    ) -> Optional[DecisionStatus]:
        """Paso 6: riesgo operacional y semántica de la operación. None si nada exige bloquear ni confirmar."""
        if risk.level == RiskLevel.CRITICAL:
            reason_codes.append("CRITICAL_OPERATIONAL_RISK")
            return DecisionStatus.BLOCK

        matched = set(operation_assessment.matched_rules or []) if operation_assessment is not None else set()
        if matched & REVIEW_RULES:
            # Borrado recursivo acotado al workspace o sobrescritura de un fichero existente con `>`: se pierde contenido
            # sin copia, así que no se veta pero lo decide una persona
            code = "WORKSPACE_DELETE_REQUIRES_REVIEW" if "RULE_WORKSPACE_DELETE_REVIEW" in matched else "OVERWRITE_EXISTING_FILE_REQUIRES_REVIEW"
            return self._confirm_or_abstain(action, action_hash, code, reason_codes)

        category = operation_assessment.category if operation_assessment is not None else None
        if category in (CommandCategory.REMOTE_MUTATION, CommandCategory.NETWORK):
            return self._confirm_or_abstain(action, action_hash, f"{category.name}_REQUIRES_CONFIRMATION", reason_codes)
        if category == CommandCategory.UNKNOWN:
            # Principio fundamental: Desconocido -> REVIEW / ABSTAIN, NUNCA BLOCK ciego
            return self._confirm_or_abstain(action, action_hash, "UNKNOWN_OPERATION_REVIEW", reason_codes)
        if tool_name and not self.registry.is_known(tool_name):
            # Herramienta desconocida no registrada requiere revisión humana explícita
            return self._confirm_or_abstain(action, action_hash, "UNKNOWN_TOOL_REQUIRES_CONFIRMATION", reason_codes)
        if risk.requires_confirmation:
            return self._confirm_or_abstain(action, action_hash, "HUMAN_CONFIRMATION_REQUIRED", reason_codes)
        return None

    def _issue_receipt(
        self,
        action: ActionCandidate,
        action_hash: str,
        state_hash: str,
        decision_id: Optional[str],
        session_id: str,
        resolved_mode: str,
        status: DecisionStatus,
        reason_codes: List[str],
        risk: RiskAssessment,
        operation_assessment: Optional[CommandRiskAssessment],
        provider_assessment: Optional[ProviderAssessment],
        latency_ms: float,
    ) -> DecisionReceipt:
        """Construye y firma el capability receipt de la decisión."""
        decision_id = decision_id or f"dec_{uuid.uuid4().hex[:12]}"
        nonce = uuid.uuid4().hex
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.receipt_ttl_seconds)

        signature = compute_receipt_signature(
            secret_key=self.secret_key,
            decision_id=decision_id,
            session_id=session_id,
            action_hash=action_hash,
            state_hash=state_hash,
            nonce=nonce,
            decision_status=status,
            expires_at=expires_at,
            execution_mode=resolved_mode,
        )

        return DecisionReceipt(
            # Contexto
            decision_id=decision_id,
            session_id=session_id,
            action_id=action.id,
            state_hash=state_hash,
            action_hash=action_hash,
            execution_mode=resolved_mode,
            nonce=nonce,
            signature=signature,
            expires_at=expires_at,
            # Proveedor
            provider_available=provider_assessment.available if provider_assessment else False,
            model_identifier=provider_assessment.model if provider_assessment else None,
            latency_ms=round(latency_ms, 2),
            # Razonamiento
            progress_score=provider_assessment.progress_probability if provider_assessment else None,
            grounded_score=provider_assessment.grounded_probability if provider_assessment else None,
            loop_type=(
                provider_assessment.reason_codes[0]
                if (provider_assessment and provider_assessment.reason_codes)
                else None
            ),
            novelty_score=provider_assessment.novelty_probability if provider_assessment else None,
            # Riesgo
            risk_level=risk.level.value if hasattr(risk.level, "value") else str(risk.level),
            risk_reasons=risk.reasons,
            destructive_potential=getattr(risk, "destructive_potential", False),
            operation_category=(
                operation_assessment.category.value
                if (operation_assessment and hasattr(operation_assessment.category, "value"))
                else (str(operation_assessment.category) if operation_assessment else None)
            ),
            operation_assessment=operation_assessment.model_dump() if operation_assessment else None,
            # Política
            decision_status=status,
            reason_codes=reason_codes,
            # Ejecución (pendiente al momento de la decisión)
            is_executed=False,
            observation_id=None,
            execution_timestamp=None,
        )

    def evaluate_action(
        self,
        action: ActionCandidate,
        state: Dict[str, Any],
        provider_assessment: Optional[ProviderAssessment] = None,
        available_evidence: Optional[List[Evidence]] = None,
        forbidden_tools: Optional[Set[str]] = None,
        completion_assessment: Optional[Any] = None,
        risk_assessment: Optional[RiskAssessment] = None,
        operation_assessment: Optional[CommandRiskAssessment] = None,
        session_id: str = "default_session",
        execution_mode: Optional[str] = None,
        decision_id: Optional[str] = None,
    ) -> Tuple[PolicyDecision, DecisionReceipt]:
        """Evalúa una acción candidata emitiendo una decisión formal y su recibo auditable."""
        start_time = time.perf_counter()
        forbidden_tools = forbidden_tools or set()
        available_evidence = available_evidence or []

        resolved_mode = self._resolve_execution_mode(state, execution_mode)
        tool_name = action.tool_call.tool_name if action.tool_call else None
        base_risk: RiskAssessment = risk_assessment or self.registry.assess_risk(tool_name)
        spec = self.registry.get_tool(tool_name) if tool_name else None
        if operation_assessment is None:
            operation_assessment = self._classify_operation(action, state)

        # Reconciliación explícita de riesgo (RiskReconciliation P0)
        risk: RiskAssessment = self.reconciler.reconcile(
            base_risk=base_risk,
            operation_assessment=operation_assessment,
            tool_name=tool_name,
            tool_spec=spec,
            action=action,
            available_evidence=available_evidence,
            execution_mode=resolved_mode,
            forbidden_tools=forbidden_tools,
        )
        is_read_only = self._is_read_only(operation_assessment, spec, tool_name)

        reason_codes: List[str] = []
        status = self._screen_action(
            action, tool_name, forbidden_tools, available_evidence, completion_assessment,
            provider_assessment, operation_assessment, risk, is_read_only, reason_codes,
        )

        action_hash = compute_action_hash(action)
        state_hash = compute_state_hash(state)
        if status is None:
            status = self._assess_operational_risk(action, action_hash, tool_name, operation_assessment, risk, reason_codes)
        if status is None:  # 7. Acción autorizada
            status = DecisionStatus.ALLOW
            reason_codes.append("GROUNDED_LOW_RISK_AUTHORIZED")

        requires_confirmation = (
            bool(risk.requires_confirmation)
            or (status == DecisionStatus.ABSTAIN)
            or bool(tool_name and not self.registry.is_known(tool_name))
            or (
                operation_assessment is not None
                and operation_assessment.category in (CommandCategory.REMOTE_MUTATION, CommandCategory.NETWORK, CommandCategory.UNKNOWN)
            )
        )
        decision = PolicyDecision(
            status=status,
            reason_codes=reason_codes,
            confidence=provider_assessment.confidence if provider_assessment else 1.0,
            forbidden_tools=list(forbidden_tools),
            requires_confirmation=requires_confirmation,
            provider=provider_assessment,
            grounding=provider_assessment.grounded_probability if provider_assessment else 1.0,
            risk=risk,
            operation_assessment=operation_assessment,
        )
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        receipt = self._issue_receipt(
            action, action_hash, state_hash, decision_id, session_id, resolved_mode, status, reason_codes,
            risk, operation_assessment, provider_assessment, latency_ms,
        )
        return decision, receipt
