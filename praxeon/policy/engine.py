"""Motor de políticas operacionales (PolicyEngine) de PRAXEON.

Aplica la separación estricta:
Semantic Judgment (JEV) != Operational Policy (PolicyEngine) != Physical Execution (Executor)

Las reglas que deciden sobre cada acción viven en ``praxeon.policy.rules`` como una cadena declarativa con orden
explícito; este módulo prepara su contexto (clasificación y reconciliación del riesgo) y firma el recibo resultante.
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
    compute_receipt_signature,
    compute_state_hash,
)
from praxeon.policy.failsafe import FailSafePolicy
from praxeon.policy.permissions import PermissionManager
from praxeon.policy.reconciliation import RiskReconciler
from praxeon.policy.registry import ToolRegistry
from praxeon.policy.rules import POLICY_CHAIN, PolicyContext, Rule, decide
from praxeon.reasoning.classifier import CommandClassifier


# Herramientas de solo lectura aunque el clasificador no se pronuncie
READ_ONLY_TOOLS = frozenset({"read_file", "view_file", "list_dir", "grep_search", "search_web", "web_search", "web_fetch", "read_url_content"})


class PolicyEngine:
    """Combina señales semánticas, evidencia empírica, riesgo y fail-safe para emitir decisiones operacionales."""

    # Cadena de reglas que decide cada acción, en orden de prioridad (ver praxeon.policy.rules)
    rules: Tuple[Rule, ...] = POLICY_CHAIN

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

    # ------------------------------------------------------------------ preparación del contexto y recibo

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

        ctx = PolicyContext(
            engine=self,
            action=action,
            tool_name=tool_name,
            risk=risk,
            operation_assessment=operation_assessment,
            provider_assessment=provider_assessment,
            available_evidence=available_evidence,
            forbidden_tools=forbidden_tools,
            completion_assessment=completion_assessment,
            is_read_only=is_read_only,
        )
        status = decide(ctx, self.rules)
        reason_codes = ctx.reason_codes
        action_hash = ctx.action_hash
        state_hash = compute_state_hash(state)

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
