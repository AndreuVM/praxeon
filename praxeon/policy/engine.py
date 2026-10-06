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
    ExecutionMode,
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


class PolicyEngine:
    """Combina señales semánticas, evidencia empírica, riesgo y fail-safe para emitir decisiones operacionales."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        failsafe: Optional[FailSafePolicy] = None,
        permission_manager: Optional[PermissionManager] = None,
        loop_threshold: float = 0.65,
        min_grounded_threshold: float = 0.35,
        min_confidence_threshold: float = 0.40,
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
        self.secret_key = secret_key or secrets.token_hex(32)
        self.receipt_ttl_seconds = receipt_ttl_seconds

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
        evidence_claims = {ev.claim.lower().strip() for ev in available_evidence}

        resolved_mode = execution_mode
        if resolved_mode is None:
            if isinstance(state, dict):
                resolved_mode = state.get("metadata", {}).get("execution_mode", "local_restricted")
            elif hasattr(state, "metadata") and isinstance(state.metadata, dict):
                resolved_mode = state.metadata.get("execution_mode", "local_restricted")
            else:
                resolved_mode = "local_restricted"

        tool_name = action.tool_call.tool_name if action.tool_call else None
        base_risk: RiskAssessment = risk_assessment or self.registry.assess_risk(tool_name)
        spec = self.registry.get_tool(tool_name) if tool_name else None

        # Clasificación contextual de la operación concreta si no fue provista externamente
        if operation_assessment is None:
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
            operation_assessment = self.classifier.classify(
                op_text, context=state if isinstance(state, dict) else None
            )

        # FASE EXPLÍCITA DE RECONCILIACIÓN DE RIESGO (RiskReconciliation P0)
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
        is_read_only = (
            (operation_assessment.read_only if operation_assessment else False)
            or (spec.read_only if spec else False)
            or (tool_name in ("read_file", "view_file", "list_dir", "grep_search", "search_web"))
        )

        reason_codes: List[str] = []
        status: Optional[DecisionStatus] = None

        # 1. Enforcement de herramientas prohibidas en el estado actual (Poda / Backtracking)
        if tool_name and tool_name in forbidden_tools:
            status = DecisionStatus.BLOCK
            reason_codes.append("TOOL_FORBIDDEN_BY_SUPERVISOR")

        # 1.5. Señal de incertidumbre para herramientas no registradas (CHG-01: Unknown != Malicious)
        elif tool_name and not self.registry.is_known(tool_name):
            reason_codes.append("UNKNOWN_TOOL_UNCERTAIN")
            reason_codes.append("UNKNOWN_TOOL_NOT_REGISTERED")

        # 2. Enforcement determinista de barreras críticas (PRIVILEGE y DESTRUCTIVE)
        if status is None:
            if operation_assessment is not None and (
                operation_assessment.category == CommandCategory.PRIVILEGE or operation_assessment.privilege_escalation
            ):
                status = DecisionStatus.BLOCK
                reason_codes.append("PRIVILEGE_ESCALATION_BLOCK")
                reason_codes.append("CRITICAL_OPERATIONAL_RISK")

            elif operation_assessment is not None and (
                operation_assessment.category == CommandCategory.DESTRUCTIVE or operation_assessment.destructive
            ):
                status = DecisionStatus.BLOCK
                reason_codes.append("DESTRUCTIVE_COMMAND_BLOCK")
                reason_codes.append("CRITICAL_OPERATIONAL_RISK")
                if provider_assessment is not None and not provider_assessment.available:
                    reason_codes.append("PROVIDER_UNAVAILABLE_DESTRUCTIVE_BLOCK")

            # 3. Verificación formal de evidencia requerida (Groundedness estricto)
            elif action.requires_evidence and any(req.lower().strip() not in evidence_claims for req in action.requires_evidence):
                missing_evidence = [
                    req for req in action.requires_evidence
                    if req.lower().strip() not in evidence_claims
                ]
                status = DecisionStatus.REPLAN
                reason_codes.append(f"MISSING_REQUIRED_EVIDENCE: {', '.join(missing_evidence)}")

            # 3.5. Verificación formal de completitud ante intentos de finish
            elif completion_assessment is not None and not getattr(completion_assessment, "is_complete", True):
                status = DecisionStatus.REPLAN
                reasons = (
                    getattr(completion_assessment, "missing_criteria", [])
                    or getattr(completion_assessment, "unverified_claims", [])
                    or [getattr(completion_assessment, "rationale", "")]
                )
                reason_codes.append(f"UNVERIFIED_COMPLETION: {', '.join(reasons)}")

            # 4. Evaluación de disponibilidad del proveedor (Fail-safe explícito - Hallazgo 3.2)
            elif provider_assessment is not None and not provider_assessment.available:
                status = self.failsafe.resolve_provider_failure(risk, is_read_only)
                if status == DecisionStatus.BLOCK:
                    reason_codes.append("PROVIDER_UNAVAILABLE_DESTRUCTIVE_BLOCK")
                elif status == DecisionStatus.ALLOW:
                    reason_codes.append("PROVIDER_UNAVAILABLE_READ_ONLY_ALLOWED")
                else:
                    reason_codes.append("PROVIDER_UNAVAILABLE_FAILSAFE_ABSTAIN")

            # 5. Evaluación semántica probabilística de JEV / LAYA (si está disponible)
            elif provider_assessment is not None:
                # Gating de confianza según JEV-as-a-Judge: juicios con baja confianza escalan a ABSTAIN
                if (
                    provider_assessment.confidence is not None
                    and provider_assessment.confidence < self.min_confidence_threshold
                ):
                    status = DecisionStatus.ABSTAIN
                    reason_codes.append(
                        f"LOW_PROVIDER_CONFIDENCE_ESCALATE ({provider_assessment.confidence:.2f} < {self.min_confidence_threshold:.2f})"
                    )

                # Clasificación de acción destructiva o peligrosa por LAYA
                elif "LAYA_DESTRUCTIVE_BLOCK" in provider_assessment.reason_codes or (
                    isinstance(provider_assessment.metadata, dict)
                    and provider_assessment.metadata.get("choice", {}).get("label") == "BLOCK"
                ):
                    status = DecisionStatus.BLOCK
                    reason_codes.append("SUPERVISOR_LAYA_DESTRUCTIVE_BLOCK")

                # Detección de bucle o degradación cíclica
                elif (
                    provider_assessment.loop_probability is not None
                    and provider_assessment.loop_probability >= self.loop_threshold
                ):
                    status = DecisionStatus.REPLAN
                    reason_codes.append(
                        f"HIGH_LOOP_PROBABILITY ({provider_assessment.loop_probability:.2f} >= {self.loop_threshold})"
                    )

                # Clasificación de acción innecesaria o desvío por LAYA
                elif "LAYA_UNNECESSARY_ACTION_REPLAN" in provider_assessment.reason_codes or (
                    isinstance(provider_assessment.metadata, dict)
                    and provider_assessment.metadata.get("choice", {}).get("label") == "REPLAN"
                ):
                    status = DecisionStatus.REPLAN
                    reason_codes.append("SUPERVISOR_LAYA_UNNECESSARY_ACTION_REPLAN")

                # Detección de premisa no fundamentada o alucinación semántica
                elif (
                    provider_assessment.grounded_probability is not None
                    and provider_assessment.grounded_probability < self.min_grounded_threshold
                ):
                    status = DecisionStatus.REPLAN
                    reason_codes.append(
                        f"LOW_GROUNDED_PROBABILITY ({provider_assessment.grounded_probability:.2f} < {self.min_grounded_threshold})"
                    )
                    if provider_assessment.reason_codes:
                        for rc in provider_assessment.reason_codes:
                            if rc not in reason_codes:
                                reason_codes.append(rc)

        # 6. Evaluación de riesgo operacional y semántica de operación
        action_hash = compute_action_hash(action)
        state_hash = compute_state_hash(state)
        mode_str = str(
            resolved_mode.value if hasattr(resolved_mode, "value") else resolved_mode or ""
        ).lower()

        if status is None:
            if risk.level == RiskLevel.CRITICAL:
                status = DecisionStatus.BLOCK
                reason_codes.append("CRITICAL_OPERATIONAL_RISK")
            elif (
                operation_assessment is not None
                and operation_assessment.category in (CommandCategory.REMOTE_MUTATION, CommandCategory.NETWORK)
            ):
                if self.permission_manager.is_action_confirmed(action.id, action_hash=action_hash):
                    status = DecisionStatus.ALLOW
                    reason_codes.append("HUMAN_CONFIRMED_ACTION")
                else:
                    status = DecisionStatus.ABSTAIN
                    reason_codes.append(f"{operation_assessment.category.name}_REQUIRES_CONFIRMATION")
            elif (
                operation_assessment is not None
                and operation_assessment.category == CommandCategory.UNKNOWN
            ):
                if self.permission_manager.is_action_confirmed(action.id, action_hash=action_hash):
                    status = DecisionStatus.ALLOW
                    reason_codes.append("HUMAN_CONFIRMED_ACTION")
                else:
                    # Principio fundamental: Desconocido -> REVIEW / ABSTAIN, NUNCA BLOCK ciego
                    status = DecisionStatus.ABSTAIN
                    reason_codes.append("UNKNOWN_OPERATION_REVIEW")
            elif tool_name and not self.registry.is_known(tool_name):
                if self.permission_manager.is_action_confirmed(action.id, action_hash=action_hash):
                    status = DecisionStatus.ALLOW
                    reason_codes.append("HUMAN_CONFIRMED_ACTION")
                else:
                    # Herramienta desconocida no registrada requiere revisión humana explícita
                    status = DecisionStatus.ABSTAIN
                    reason_codes.append("UNKNOWN_TOOL_REQUIRES_CONFIRMATION")
            elif risk.requires_confirmation:
                if self.permission_manager.is_action_confirmed(action.id, action_hash=action_hash):
                    status = DecisionStatus.ALLOW
                    reason_codes.append("HUMAN_CONFIRMED_ACTION")
                else:
                    status = DecisionStatus.ABSTAIN
                    reason_codes.append("HUMAN_CONFIRMATION_REQUIRED")

        # 7. Acción autorizada (ALLOW)
        if status is None:
            status = DecisionStatus.ALLOW
            reason_codes.append("GROUNDED_LOW_RISK_AUTHORIZED")

        confidence = provider_assessment.confidence if provider_assessment else 1.0
        grounding = provider_assessment.grounded_probability if provider_assessment else 1.0

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
            confidence=confidence,
            forbidden_tools=list(forbidden_tools),
            requires_confirmation=requires_confirmation,
            provider=provider_assessment,
            grounding=grounding,
            risk=risk,
            operation_assessment=operation_assessment,
        )

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        # Construcción y firma criptográfica exhaustiva del capability receipt
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

        receipt = DecisionReceipt(
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

        return decision, receipt
