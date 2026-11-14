"""Cadena declarativa de reglas de PolicyEngine.evaluate_action (auditoría A-01).

Cada regla es una clase pequeña con ``apply(ctx) -> Optional[DecisionStatus]``: devuelve un veredicto si le corresponde
decidir, o None para ceder el turno a la siguiente. Algunas reglas solo anotan un motivo en ``ctx.reason_codes`` y nunca
deciden. El orden de ``POLICY_CHAIN`` es la prioridad: decide la primera regla que devuelve un veredicto.

Grupos, en orden:

1. ``SCREENING_RULES``: barreras deterministas (herramienta prohibida, privilegios, destrucción, evidencia, completitud)
   y fail-safe ante un supervisor no disponible.
2. ``SUPERVISOR_RULES``: juicio del supervisor semántico (LAYA, TypeSafe, KEV...) cuando está disponible.
3. ``OPERATIONAL_RISK_RULES``: riesgo operacional reconciliado; bloquea lo crítico y pide confirmación humana al resto.
4. ``AuthorizeRule``: si ninguna regla decidió, la acción se autoriza.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Iterable, List, Optional, Set

from praxeon.domain.models import (
    ActionCandidate,
    CommandCategory,
    CommandRiskAssessment,
    DecisionStatus,
    Evidence,
    ProviderAssessment,
    RiskAssessment,
    RiskLevel,
    compute_action_hash,
)

if TYPE_CHECKING:
    from praxeon.policy.engine import PolicyEngine


# Reglas del clasificador que no se vetan pero exigen que una persona confirme (se pierde contenido sin copia)
REVIEW_RULES = frozenset({"RULE_WORKSPACE_DELETE_REVIEW", "RULE_OVERWRITE_EXISTING_FILE"})

# Proveedores cuya confianza es la probabilidad de la opción elegida (la de TypeSafe está normalizada)
CHOICE_PROBABILITY_PROVIDERS = ("laya", "kev")


@dataclass(frozen=True)
class SupervisorReading:
    """Lectura del juicio del supervisor que comparten varias reglas del grupo SUPERVISOR_RULES."""

    label: Optional[str]
    advisory: bool
    weak_veto: bool
    advisory_review: bool
    p_block: float
    provider_tag: str

    @classmethod
    def read(cls, provider: ProviderAssessment, engine: "PolicyEngine") -> "SupervisorReading":
        metadata: Dict[str, Any] = provider.metadata if isinstance(provider.metadata, dict) else {}
        choice = metadata.get("choice", {})
        label = choice.get("label")
        weak_veto = (
            str(getattr(provider, "provider", "")).lower() in CHOICE_PROBABILITY_PROVIDERS
            and label in ("BLOCK", "REPLAN")
            and provider.confidence is not None
            and provider.confidence < engine.veto_min_confidence
        )
        # Supervisor consultivo (p. ej. LAYA sin ajuste fino): su etiqueta es una señal de aviso, no un veto. Un BLOCK
        # probable pide revisión humana, un REPLAN se ignora, y su confianza no sirve de umbral de escalado.
        advisory = metadata.get("veto_mode") == "advisory"
        probs = choice.get("raw_probabilities") or choice.get("probabilities") or {}
        p_block = float(probs.get("BLOCK", 0.0))
        return cls(
            label=label,
            advisory=advisory,
            weak_veto=weak_veto,
            advisory_review=bool(advisory and label == "BLOCK" and p_block >= engine.advisory_review_min_probability),
            p_block=p_block,
            provider_tag=str(getattr(provider, "provider", "") or "supervisor").upper(),
        )

    @property
    def can_veto(self) -> bool:
        """Solo un supervisor no consultivo y con confianza suficiente puede vetar por sí solo."""
        return not self.advisory and not self.weak_veto


@dataclass
class PolicyContext:
    """Todo lo que las reglas necesitan para decidir sobre una acción; ``reason_codes`` acumula los motivos."""

    engine: "PolicyEngine"
    action: ActionCandidate
    tool_name: Optional[str]
    risk: RiskAssessment
    operation_assessment: Optional[CommandRiskAssessment] = None
    provider_assessment: Optional[ProviderAssessment] = None
    available_evidence: List[Evidence] = field(default_factory=list)
    forbidden_tools: Set[str] = field(default_factory=set)
    completion_assessment: Optional[Any] = None
    is_read_only: bool = False
    reason_codes: List[str] = field(default_factory=list)

    @cached_property
    def action_hash(self) -> str:
        return compute_action_hash(self.action)

    @cached_property
    def supervisor(self) -> Optional[SupervisorReading]:
        """Lectura del supervisor, o None si no hay supervisor o no está disponible (eso lo decide el fail-safe)."""
        provider = self.provider_assessment
        if provider is None or not provider.available:
            return None
        return SupervisorReading.read(provider, self.engine)

    @property
    def tool_is_unknown(self) -> bool:
        return bool(self.tool_name and not self.engine.registry.is_known(self.tool_name))

    @property
    def category(self) -> Optional[CommandCategory]:
        return self.operation_assessment.category if self.operation_assessment is not None else None


class Rule(ABC):
    """Una regla de política: devuelve un veredicto si le toca decidir, o None para seguir con la siguiente."""

    name: ClassVar[str]

    @abstractmethod
    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        """Evalúa la regla; puede añadir motivos a ``ctx.reason_codes`` aunque no decida."""

    def __repr__(self) -> str:
        return f"<Rule {self.name}>"


def run_rules(rules: Iterable[Rule], ctx: PolicyContext) -> Optional[DecisionStatus]:
    """Aplica las reglas en orden y devuelve el primer veredicto, o None si ninguna decide."""
    for rule in rules:
        status = rule.apply(ctx)
        if status is not None:
            return status
    return None


# ---------------------------------------------------------------------------------------------- 1. barreras deterministas


class ForbiddenToolRule(Rule):
    """Herramienta prohibida por el supervisor en el estado actual (poda / backtracking): BLOCK."""

    name = "forbidden_tool"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        if ctx.tool_name and ctx.tool_name in ctx.forbidden_tools:
            ctx.reason_codes.append("TOOL_FORBIDDEN_BY_SUPERVISOR")
            return DecisionStatus.BLOCK
        return None


class UnknownToolSignalRule(Rule):
    """Anota la incertidumbre de una herramienta no registrada (CHG-01: desconocido != malicioso). Nunca decide."""

    name = "unknown_tool_signal"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        if ctx.tool_is_unknown:
            ctx.reason_codes.append("UNKNOWN_TOOL_UNCERTAIN")
            ctx.reason_codes.append("UNKNOWN_TOOL_NOT_REGISTERED")
        return None


class PrivilegeEscalationRule(Rule):
    """Escalada de privilegios según el clasificador: BLOCK."""

    name = "privilege_escalation"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        op = ctx.operation_assessment
        if op is not None and (op.category == CommandCategory.PRIVILEGE or op.privilege_escalation):
            ctx.reason_codes.append("PRIVILEGE_ESCALATION_BLOCK")
            ctx.reason_codes.append("CRITICAL_OPERATIONAL_RISK")
            return DecisionStatus.BLOCK
        return None


class DestructiveCommandRule(Rule):
    """Operación destructiva según el clasificador: BLOCK."""

    name = "destructive_command"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        op = ctx.operation_assessment
        if op is not None and (op.category == CommandCategory.DESTRUCTIVE or op.destructive):
            ctx.reason_codes.append("DESTRUCTIVE_COMMAND_BLOCK")
            ctx.reason_codes.append("CRITICAL_OPERATIONAL_RISK")
            if ctx.provider_assessment is not None and not ctx.provider_assessment.available:
                ctx.reason_codes.append("PROVIDER_UNAVAILABLE_DESTRUCTIVE_BLOCK")
            return DecisionStatus.BLOCK
        return None


class RequiredEvidenceRule(Rule):
    """Falta evidencia que la acción declara necesaria (groundedness estricto): REPLAN."""

    name = "required_evidence"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        claims = {ev.claim.lower().strip() for ev in ctx.available_evidence}
        missing = [req for req in ctx.action.requires_evidence or [] if req.lower().strip() not in claims]
        if missing:
            ctx.reason_codes.append(f"MISSING_REQUIRED_EVIDENCE: {', '.join(missing)}")
            return DecisionStatus.REPLAN
        return None


class UnverifiedCompletionRule(Rule):
    """Intento de terminar sin cumplir los criterios de completitud: REPLAN."""

    name = "unverified_completion"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        completion = ctx.completion_assessment
        if completion is None or getattr(completion, "is_complete", True):
            return None
        reasons = (
            getattr(completion, "missing_criteria", [])
            or getattr(completion, "unverified_claims", [])
            or [getattr(completion, "rationale", "")]
        )
        ctx.reason_codes.append(f"UNVERIFIED_COMPLETION: {', '.join(reasons)}")
        return DecisionStatus.REPLAN


class ProviderUnavailableRule(Rule):
    """Supervisor caído: decide el fail-safe, que nunca convierte la caída en un ALLOW por accidente."""

    name = "provider_unavailable"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        provider = ctx.provider_assessment
        if provider is None or provider.available:
            return None
        status = ctx.engine.failsafe.resolve_provider_failure(ctx.risk, ctx.is_read_only)
        if status == DecisionStatus.BLOCK:
            ctx.reason_codes.append("PROVIDER_UNAVAILABLE_DESTRUCTIVE_BLOCK")
        elif status == DecisionStatus.ALLOW:
            ctx.reason_codes.append("PROVIDER_UNAVAILABLE_READ_ONLY_ALLOWED")
        else:
            ctx.reason_codes.append("PROVIDER_UNAVAILABLE_FAILSAFE_ABSTAIN")
        return status


# ---------------------------------------------------------------------------------------------- 2. supervisor semántico


class SupervisorNoticesRule(Rule):
    """Anota los vetos que no cuentan: los de un supervisor consultivo y los de baja confianza. Nunca decide."""

    name = "supervisor_notices"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        reading, provider = ctx.supervisor, ctx.provider_assessment
        if reading is None or provider is None:
            return None
        if reading.advisory and reading.label in ("BLOCK", "REPLAN") and not reading.advisory_review:
            ctx.reason_codes.append(f"ADVISORY_{reading.label}_IGNORED")
        if reading.weak_veto and not reading.advisory:
            ctx.reason_codes.append(
                f"WEAK_{reading.label}_VETO_DEFERRED ({provider.confidence:.2f} < {ctx.engine.veto_min_confidence:.2f})"
            )
        return None


class AdvisoryReviewRule(Rule):
    """Un supervisor consultivo ve un BLOCK probable: lo decide una persona (ABSTAIN)."""

    name = "advisory_review"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        reading = ctx.supervisor
        if reading is None or not reading.advisory_review:
            return None
        ctx.reason_codes.append(
            f"SUPERVISOR_{reading.provider_tag}_ADVISORY_REVIEW "
            f"(p_block={reading.p_block:.2f} >= {ctx.engine.advisory_review_min_probability:.2f})"
        )
        return DecisionStatus.ABSTAIN


class LowProviderConfidenceRule(Rule):
    """Juicio con baja confianza (JEV-as-a-Judge): escala a una persona (ABSTAIN)."""

    name = "low_provider_confidence"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        reading, provider = ctx.supervisor, ctx.provider_assessment
        if reading is None or provider is None or reading.advisory:
            return None
        if provider.confidence is not None and provider.confidence < ctx.engine.min_confidence_threshold:
            ctx.reason_codes.append(
                f"LOW_PROVIDER_CONFIDENCE_ESCALATE ({provider.confidence:.2f} < {ctx.engine.min_confidence_threshold:.2f})"
            )
            return DecisionStatus.ABSTAIN
        return None


class SupervisorBlockRule(Rule):
    """El supervisor veta la acción por destructiva o peligrosa: BLOCK.

    Un veto con probabilidad calibrada baja es ruido de un modelo que duda: no veta por sí solo, decide el análisis
    determinista de riesgo (OPERATIONAL_RISK_RULES).
    """

    name = "supervisor_block"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        reading, provider = ctx.supervisor, ctx.provider_assessment
        if reading is None or provider is None or not reading.can_veto:
            return None
        if "LAYA_DESTRUCTIVE_BLOCK" in provider.reason_codes or reading.label == "BLOCK":
            ctx.reason_codes.append("SUPERVISOR_LAYA_DESTRUCTIVE_BLOCK")
            return DecisionStatus.BLOCK
        return None


class LoopDetectionRule(Rule):
    """Probabilidad alta de bucle o degradación cíclica: REPLAN."""

    name = "loop_detection"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        provider = ctx.provider_assessment
        if ctx.supervisor is None or provider is None:
            return None
        if provider.loop_probability is not None and provider.loop_probability >= ctx.engine.loop_threshold:
            ctx.reason_codes.append(f"HIGH_LOOP_PROBABILITY ({provider.loop_probability:.2f} >= {ctx.engine.loop_threshold})")
            return DecisionStatus.REPLAN
        return None


class SupervisorReplanRule(Rule):
    """El supervisor juzga la acción innecesaria o un desvío: REPLAN."""

    name = "supervisor_replan"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        reading, provider = ctx.supervisor, ctx.provider_assessment
        if reading is None or provider is None or not reading.can_veto:
            return None
        if "LAYA_UNNECESSARY_ACTION_REPLAN" in provider.reason_codes or reading.label == "REPLAN":
            ctx.reason_codes.append("SUPERVISOR_LAYA_UNNECESSARY_ACTION_REPLAN")
            return DecisionStatus.REPLAN
        return None


class LowGroundingRule(Rule):
    """Premisa no fundamentada o alucinación semántica: REPLAN.

    Un supervisor consultivo solo avisa: si vetara por esto, bloquearía hasta las lecturas con las que el agente reúne
    la evidencia que se le exige.
    """

    name = "low_grounding"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        reading, provider = ctx.supervisor, ctx.provider_assessment
        if reading is None or provider is None or reading.advisory:
            return None
        grounded = provider.grounded_probability
        if grounded is None or grounded >= ctx.engine.min_grounded_threshold:
            return None
        ctx.reason_codes.append(f"LOW_GROUNDED_PROBABILITY ({grounded:.2f} < {ctx.engine.min_grounded_threshold})")
        for code in provider.reason_codes or []:
            if code not in ctx.reason_codes:
                ctx.reason_codes.append(code)
        return DecisionStatus.REPLAN


# ---------------------------------------------------------------------------------------------- 3. riesgo operacional


class CriticalRiskRule(Rule):
    """Riesgo operacional reconciliado CRITICAL: BLOCK."""

    name = "critical_risk"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        if ctx.risk.level == RiskLevel.CRITICAL:
            ctx.reason_codes.append("CRITICAL_OPERATIONAL_RISK")
            return DecisionStatus.BLOCK
        return None


class ConfirmationRule(Rule):
    """Regla que pide confirmación humana: ALLOW si una persona ya confirmó esta acción exacta, si no ABSTAIN."""

    @abstractmethod
    def abstain_code(self, ctx: PolicyContext) -> Optional[str]:
        """Motivo por el que la acción necesita confirmación, o None si esta regla no la pide."""

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        code = self.abstain_code(ctx)
        if code is None:
            return None
        return confirm_or_abstain(ctx, code)


def confirm_or_abstain(ctx: PolicyContext, abstain_code: str) -> DecisionStatus:
    """ALLOW si una persona ya confirmó esta acción exacta (mismo hash); si no, ABSTAIN con el motivo dado."""
    if ctx.engine.permission_manager.is_action_confirmed(ctx.action.id, action_hash=ctx.action_hash):
        ctx.reason_codes.append("HUMAN_CONFIRMED_ACTION")
        return DecisionStatus.ALLOW
    ctx.reason_codes.append(abstain_code)
    return DecisionStatus.ABSTAIN


class ContentLossReviewRule(ConfirmationRule):
    """Borrado recursivo acotado al workspace o sobrescritura de un fichero existente con ``>``.

    Se pierde contenido sin copia, así que no se veta pero lo decide una persona.
    """

    name = "content_loss_review"

    def abstain_code(self, ctx: PolicyContext) -> Optional[str]:
        op = ctx.operation_assessment
        matched = set(op.matched_rules or []) if op is not None else set()
        if not matched & REVIEW_RULES:
            return None
        if "RULE_WORKSPACE_DELETE_REVIEW" in matched:
            return "WORKSPACE_DELETE_REQUIRES_REVIEW"
        return "OVERWRITE_EXISTING_FILE_REQUIRES_REVIEW"


class ExternalEffectConfirmationRule(ConfirmationRule):
    """Mutación remota o acceso a red: lo confirma una persona."""

    name = "external_effect_confirmation"

    def abstain_code(self, ctx: PolicyContext) -> Optional[str]:
        category = ctx.category
        if category in (CommandCategory.REMOTE_MUTATION, CommandCategory.NETWORK):
            return f"{category.name}_REQUIRES_CONFIRMATION"
        return None


class UnknownOperationRule(ConfirmationRule):
    """Operación que el clasificador no reconoce. Principio fundamental: desconocido -> revisión, nunca BLOCK ciego."""

    name = "unknown_operation"

    def abstain_code(self, ctx: PolicyContext) -> Optional[str]:
        return "UNKNOWN_OPERATION_REVIEW" if ctx.category == CommandCategory.UNKNOWN else None


class UnknownToolConfirmationRule(ConfirmationRule):
    """Herramienta no registrada: requiere revisión humana explícita."""

    name = "unknown_tool_confirmation"

    def abstain_code(self, ctx: PolicyContext) -> Optional[str]:
        return "UNKNOWN_TOOL_REQUIRES_CONFIRMATION" if ctx.tool_is_unknown else None


class RiskConfirmationRule(ConfirmationRule):
    """El riesgo reconciliado exige confirmación humana."""

    name = "risk_confirmation"

    def abstain_code(self, ctx: PolicyContext) -> Optional[str]:
        return "HUMAN_CONFIRMATION_REQUIRED" if ctx.risk.requires_confirmation else None


# ---------------------------------------------------------------------------------------------- 4. autorización


class AuthorizeRule(Rule):
    """Ninguna regla anterior decidió: acción fundamentada y de bajo riesgo, ALLOW."""

    name = "authorize"

    def apply(self, ctx: PolicyContext) -> Optional[DecisionStatus]:
        ctx.reason_codes.append("GROUNDED_LOW_RISK_AUTHORIZED")
        return DecisionStatus.ALLOW


# ---------------------------------------------------------------------------------------------- la cadena

SCREENING_RULES: tuple[Rule, ...] = (
    ForbiddenToolRule(),
    UnknownToolSignalRule(),
    PrivilegeEscalationRule(),
    DestructiveCommandRule(),
    RequiredEvidenceRule(),
    UnverifiedCompletionRule(),
    ProviderUnavailableRule(),
)

SUPERVISOR_RULES: tuple[Rule, ...] = (
    SupervisorNoticesRule(),
    AdvisoryReviewRule(),
    LowProviderConfidenceRule(),
    SupervisorBlockRule(),
    LoopDetectionRule(),
    SupervisorReplanRule(),
    LowGroundingRule(),
)

OPERATIONAL_RISK_RULES: tuple[Rule, ...] = (
    CriticalRiskRule(),
    ContentLossReviewRule(),
    ExternalEffectConfirmationRule(),
    UnknownOperationRule(),
    UnknownToolConfirmationRule(),
    RiskConfirmationRule(),
)

# Orden explícito: es la prioridad. AuthorizeRule va al final y siempre decide, así que la cadena nunca devuelve None.
POLICY_CHAIN: tuple[Rule, ...] = (*SCREENING_RULES, *SUPERVISOR_RULES, *OPERATIONAL_RISK_RULES, AuthorizeRule())


def decide(ctx: PolicyContext, rules: Iterable[Rule] = POLICY_CHAIN) -> DecisionStatus:
    """Aplica la cadena completa y devuelve su veredicto.

    Una cadena que no decide es un error de configuración (le falta AuthorizeRule al final): se rechaza en lugar de
    autorizar por omisión.
    """
    status = run_rules(rules, ctx)
    if status is None:
        raise ValueError("La cadena de reglas no emitió veredicto: debe terminar en una regla que siempre decida")
    return status
