"""Cadena declarativa de reglas de PolicyEngine: cada regla por separado y su orden (auditoría A-01)."""

import pytest

from praxeon.domain.models import (
    ActionCandidate,
    CommandCategory,
    CommandRiskAssessment,
    DecisionStatus,
    Evidence,
    ProviderAssessment,
    RiskAssessment,
    RiskLevel,
    ToolCall,
)
from praxeon.policy.engine import PolicyEngine
from praxeon.policy.failsafe import FailSafePolicy
from praxeon.policy import rules as R


@pytest.fixture
def engine():
    return PolicyEngine()


def _action(command="ls", tool="run_command", **kw):
    return ActionCandidate(id="a1", description=command, tool_call=ToolCall(tool_name=tool, arguments={"command": command}), **kw)


def _op(category=CommandCategory.INSPECTION, **kw):
    return CommandRiskAssessment(operation="op", category=category, risk_level=RiskLevel.LOW, **kw)


def _risk(level=RiskLevel.LOW, **kw):
    return RiskAssessment(level=level, **kw)


def _ctx(engine, action=None, tool="run_command", risk=None, op=None, **kw):
    return R.PolicyContext(
        engine=engine,
        action=action or _action(tool=tool),
        tool_name=tool,
        risk=risk or _risk(),
        operation_assessment=op or _op(),
        **kw,
    )


def _supervisor(**kw):
    kw.setdefault("provider", "typesafe")
    kw.setdefault("confidence", 0.9)
    return ProviderAssessment(**kw)


def _choice(label, p_block=None, advisory=False):
    choice = {"label": label}
    if p_block is not None:
        choice["raw_probabilities"] = {"BLOCK": p_block}
    metadata = {"choice": choice}
    if advisory:
        metadata["veto_mode"] = "advisory"
    return metadata


# ------------------------------------------------------------------------------------------------ la cadena


def test_chain_order_is_explicit_and_ends_authorizing(engine):
    assert [rule.name for rule in R.POLICY_CHAIN] == [
        "forbidden_tool", "unknown_tool_signal", "privilege_escalation", "destructive_command", "required_evidence",
        "unverified_completion", "provider_unavailable",
        "supervisor_notices", "advisory_review", "low_provider_confidence", "supervisor_block", "loop_detection",
        "supervisor_replan", "low_grounding",
        "critical_risk", "content_loss_review", "external_effect_confirmation", "unknown_operation",
        "unknown_tool_confirmation", "risk_confirmation",
        "authorize",
    ]
    assert engine.rules is R.POLICY_CHAIN


def test_a_chain_that_does_not_decide_is_rejected_instead_of_allowing(engine):
    with pytest.raises(ValueError):
        R.decide(_ctx(engine), rules=())


def test_the_first_rule_that_decides_wins(engine):
    # Herramienta prohibida y además destructiva: solo cuenta la prohibición, que va antes
    ctx = _ctx(engine, op=_op(CommandCategory.DESTRUCTIVE, destructive=True), forbidden_tools={"run_command"})
    assert R.decide(ctx) == DecisionStatus.BLOCK
    assert ctx.reason_codes == ["TOOL_FORBIDDEN_BY_SUPERVISOR"]


def test_a_supervisor_veto_comes_before_asking_for_confirmation(engine):
    ctx = _ctx(engine, op=_op(CommandCategory.NETWORK), provider_assessment=_supervisor(metadata=_choice("BLOCK")))
    assert R.decide(ctx) == DecisionStatus.BLOCK
    assert ctx.reason_codes == ["SUPERVISOR_LAYA_DESTRUCTIVE_BLOCK"]


def test_nothing_against_a_low_risk_action_authorizes_it(engine):
    ctx = _ctx(engine, provider_assessment=_supervisor())
    assert R.decide(ctx) == DecisionStatus.ALLOW
    assert ctx.reason_codes == ["GROUNDED_LOW_RISK_AUTHORIZED"]


# ------------------------------------------------------------------------------------------------ 1. barreras deterministas


def test_forbidden_tool(engine):
    rule = R.ForbiddenToolRule()
    assert rule.apply(_ctx(engine)) is None
    ctx = _ctx(engine, forbidden_tools={"run_command"})
    assert rule.apply(ctx) == DecisionStatus.BLOCK and ctx.reason_codes == ["TOOL_FORBIDDEN_BY_SUPERVISOR"]


def test_unknown_tool_signal_only_annotates(engine):
    rule = R.UnknownToolSignalRule()
    ctx = _ctx(engine, tool="not_a_registered_tool")
    assert rule.apply(ctx) is None
    assert ctx.reason_codes == ["UNKNOWN_TOOL_UNCERTAIN", "UNKNOWN_TOOL_NOT_REGISTERED"]
    ctx = _ctx(engine)
    assert rule.apply(ctx) is None and ctx.reason_codes == []


@pytest.mark.parametrize("op", [_op(CommandCategory.PRIVILEGE), _op(CommandCategory.LOCAL_MUTATION, privilege_escalation=True)])
def test_privilege_escalation(engine, op):
    ctx = _ctx(engine, op=op)
    assert R.PrivilegeEscalationRule().apply(ctx) == DecisionStatus.BLOCK
    assert ctx.reason_codes == ["PRIVILEGE_ESCALATION_BLOCK", "CRITICAL_OPERATIONAL_RISK"]
    assert R.PrivilegeEscalationRule().apply(_ctx(engine)) is None


def test_destructive_command(engine):
    rule = R.DestructiveCommandRule()
    assert rule.apply(_ctx(engine)) is None
    ctx = _ctx(engine, op=_op(CommandCategory.DESTRUCTIVE))
    assert rule.apply(ctx) == DecisionStatus.BLOCK
    assert ctx.reason_codes == ["DESTRUCTIVE_COMMAND_BLOCK", "CRITICAL_OPERATIONAL_RISK"]
    # con el supervisor caído se deja constancia también de eso
    ctx = _ctx(engine, op=_op(CommandCategory.LOCAL_MUTATION, destructive=True),
               provider_assessment=ProviderAssessment(provider="laya", available=False))
    assert rule.apply(ctx) == DecisionStatus.BLOCK
    assert ctx.reason_codes[-1] == "PROVIDER_UNAVAILABLE_DESTRUCTIVE_BLOCK"


def test_required_evidence(engine):
    rule = R.RequiredEvidenceRule()
    action = _action(requires_evidence=["Hecho A", "hecho b"])
    ctx = _ctx(engine, action=action, available_evidence=[Evidence(id="e1", claim=" hecho a ")])
    assert rule.apply(ctx) == DecisionStatus.REPLAN
    assert ctx.reason_codes == ["MISSING_REQUIRED_EVIDENCE: hecho b"]
    # la comparación ignora mayúsculas y espacios
    ctx = _ctx(engine, action=action, available_evidence=[Evidence(id="e1", claim="hecho a"), Evidence(id="e2", claim="HECHO B")])
    assert rule.apply(ctx) is None and ctx.reason_codes == []


def test_unverified_completion(engine):
    class Completion:
        def __init__(self, complete):
            self.is_complete, self.missing_criteria = complete, ["tests en verde"]

    rule = R.UnverifiedCompletionRule()
    assert rule.apply(_ctx(engine)) is None
    assert rule.apply(_ctx(engine, completion_assessment=Completion(True))) is None
    ctx = _ctx(engine, completion_assessment=Completion(False))
    assert rule.apply(ctx) == DecisionStatus.REPLAN and ctx.reason_codes == ["UNVERIFIED_COMPLETION: tests en verde"]


def test_provider_unavailable_defers_to_the_failsafe(engine):
    rule, down = R.ProviderUnavailableRule(), ProviderAssessment(provider="laya", available=False)
    assert rule.apply(_ctx(engine)) is None
    assert rule.apply(_ctx(engine, provider_assessment=_supervisor())) is None

    ctx = _ctx(engine, provider_assessment=down)
    assert rule.apply(ctx) == DecisionStatus.ABSTAIN and ctx.reason_codes == ["PROVIDER_UNAVAILABLE_FAILSAFE_ABSTAIN"]

    ctx = _ctx(engine, provider_assessment=down, risk=_risk(RiskLevel.CRITICAL))
    assert rule.apply(ctx) == DecisionStatus.BLOCK and ctx.reason_codes == ["PROVIDER_UNAVAILABLE_DESTRUCTIVE_BLOCK"]

    permissive = PolicyEngine(failsafe=FailSafePolicy(allow_read_only_on_provider_failure=True))
    ctx = _ctx(permissive, provider_assessment=down, is_read_only=True)
    assert rule.apply(ctx) == DecisionStatus.ALLOW and ctx.reason_codes == ["PROVIDER_UNAVAILABLE_READ_ONLY_ALLOWED"]


# ------------------------------------------------------------------------------------------------ 2. supervisor


@pytest.mark.parametrize("rule", R.SUPERVISOR_RULES, ids=lambda rule: rule.name)
def test_supervisor_rules_do_not_apply_without_an_available_supervisor(engine, rule):
    vetoing = dict(confidence=0.0, loop_probability=1.0, grounded_probability=0.0, metadata=_choice("BLOCK", 0.9, advisory=True))
    for provider in (None, ProviderAssessment(provider="laya", available=False, **vetoing)):
        ctx = _ctx(engine, provider_assessment=provider)
        assert rule.apply(ctx) is None and ctx.reason_codes == []


def test_supervisor_notices_record_the_vetoes_that_do_not_count(engine):
    rule = R.SupervisorNoticesRule()
    ctx = _ctx(engine, provider_assessment=_supervisor(metadata=_choice("REPLAN", advisory=True)))
    assert rule.apply(ctx) is None and ctx.reason_codes == ["ADVISORY_REPLAN_IGNORED"]
    ctx = _ctx(engine, provider_assessment=_supervisor(provider="laya", confidence=0.3, metadata=_choice("BLOCK")))
    assert rule.apply(ctx) is None and ctx.reason_codes == ["WEAK_BLOCK_VETO_DEFERRED (0.30 < 0.50)"]
    # TypeSafe normaliza su confianza: una baja no convierte su veto en débil
    ctx = _ctx(engine, provider_assessment=_supervisor(confidence=0.3, metadata=_choice("BLOCK")))
    assert rule.apply(ctx) is None and ctx.reason_codes == []


def test_advisory_review(engine):
    rule = R.AdvisoryReviewRule()
    ctx = _ctx(engine, provider_assessment=_supervisor(provider="laya", metadata=_choice("BLOCK", 0.9, advisory=True)))
    assert rule.apply(ctx) == DecisionStatus.ABSTAIN
    assert ctx.reason_codes == ["SUPERVISOR_LAYA_ADVISORY_REVIEW (p_block=0.90 >= 0.40)"]
    assert rule.apply(_ctx(engine, provider_assessment=_supervisor(metadata=_choice("BLOCK", 0.1, advisory=True)))) is None
    assert rule.apply(_ctx(engine, provider_assessment=_supervisor(metadata=_choice("BLOCK", 0.9)))) is None


def test_low_provider_confidence(engine):
    rule = R.LowProviderConfidenceRule()
    ctx = _ctx(engine, provider_assessment=_supervisor(confidence=0.05))
    assert rule.apply(ctx) == DecisionStatus.ABSTAIN and ctx.reason_codes == ["LOW_PROVIDER_CONFIDENCE_ESCALATE (0.05 < 0.30)"]
    assert rule.apply(_ctx(engine, provider_assessment=_supervisor())) is None
    # la confianza de un supervisor consultivo no sirve de umbral de escalado
    assert rule.apply(_ctx(engine, provider_assessment=_supervisor(confidence=0.05, metadata={"veto_mode": "advisory"}))) is None


def test_supervisor_block(engine):
    rule = R.SupervisorBlockRule()
    for provider in (_supervisor(metadata=_choice("BLOCK")), _supervisor(reason_codes=["LAYA_DESTRUCTIVE_BLOCK"])):
        ctx = _ctx(engine, provider_assessment=provider)
        assert rule.apply(ctx) == DecisionStatus.BLOCK and ctx.reason_codes == ["SUPERVISOR_LAYA_DESTRUCTIVE_BLOCK"]
    # un veto débil o consultivo no bloquea por sí solo
    assert rule.apply(_ctx(engine, provider_assessment=_supervisor(provider="laya", confidence=0.3, metadata=_choice("BLOCK")))) is None
    assert rule.apply(_ctx(engine, provider_assessment=_supervisor(metadata=_choice("BLOCK", advisory=True)))) is None
    assert rule.apply(_ctx(engine, provider_assessment=_supervisor(metadata=_choice("ALLOW")))) is None


def test_loop_detection(engine):
    rule = R.LoopDetectionRule()
    ctx = _ctx(engine, provider_assessment=_supervisor(loop_probability=0.95))
    assert rule.apply(ctx) == DecisionStatus.REPLAN and ctx.reason_codes == ["HIGH_LOOP_PROBABILITY (0.95 >= 0.65)"]
    assert rule.apply(_ctx(engine, provider_assessment=_supervisor(loop_probability=0.2))) is None
    assert rule.apply(_ctx(engine, provider_assessment=_supervisor())) is None


def test_supervisor_replan(engine):
    rule = R.SupervisorReplanRule()
    for provider in (_supervisor(metadata=_choice("REPLAN")), _supervisor(reason_codes=["LAYA_UNNECESSARY_ACTION_REPLAN"])):
        ctx = _ctx(engine, provider_assessment=provider)
        assert rule.apply(ctx) == DecisionStatus.REPLAN and ctx.reason_codes == ["SUPERVISOR_LAYA_UNNECESSARY_ACTION_REPLAN"]
    assert rule.apply(_ctx(engine, provider_assessment=_supervisor(metadata=_choice("REPLAN", advisory=True)))) is None
    assert rule.apply(_ctx(engine, provider_assessment=_supervisor(provider="kev", confidence=0.2, metadata=_choice("REPLAN")))) is None


def test_low_grounding(engine):
    rule = R.LowGroundingRule()
    provider = _supervisor(grounded_probability=0.1, reason_codes=["PREMISA_X", "PREMISA_X", "PREMISA_Y"])
    ctx = _ctx(engine, provider_assessment=provider)
    assert rule.apply(ctx) == DecisionStatus.REPLAN
    assert ctx.reason_codes == ["LOW_GROUNDED_PROBABILITY (0.10 < 0.35)", "PREMISA_X", "PREMISA_Y"]
    assert rule.apply(_ctx(engine, provider_assessment=_supervisor(grounded_probability=0.8))) is None
    # un supervisor consultivo solo avisa: no bloquea las lecturas con las que se reúne la evidencia
    advisory = _supervisor(grounded_probability=0.1, metadata={"veto_mode": "advisory"})
    assert rule.apply(_ctx(engine, provider_assessment=advisory)) is None


# ------------------------------------------------------------------------------------------------ 3. riesgo operacional


def test_critical_risk(engine):
    ctx = _ctx(engine, risk=_risk(RiskLevel.CRITICAL))
    assert R.CriticalRiskRule().apply(ctx) == DecisionStatus.BLOCK and ctx.reason_codes == ["CRITICAL_OPERATIONAL_RISK"]
    assert R.CriticalRiskRule().apply(_ctx(engine, risk=_risk(RiskLevel.HIGH))) is None


@pytest.mark.parametrize(
    "rule, ctx_kwargs, code",
    [
        (R.ContentLossReviewRule(), {"op": _op(CommandCategory.LOCAL_MUTATION, matched_rules=["RULE_WORKSPACE_DELETE_REVIEW"])},
         "WORKSPACE_DELETE_REQUIRES_REVIEW"),
        (R.ContentLossReviewRule(), {"op": _op(CommandCategory.LOCAL_MUTATION, matched_rules=["RULE_OVERWRITE_EXISTING_FILE"])},
         "OVERWRITE_EXISTING_FILE_REQUIRES_REVIEW"),
        (R.ExternalEffectConfirmationRule(), {"op": _op(CommandCategory.NETWORK)}, "NETWORK_REQUIRES_CONFIRMATION"),
        (R.ExternalEffectConfirmationRule(), {"op": _op(CommandCategory.REMOTE_MUTATION)}, "REMOTE_MUTATION_REQUIRES_CONFIRMATION"),
        (R.UnknownOperationRule(), {"op": _op(CommandCategory.UNKNOWN)}, "UNKNOWN_OPERATION_REVIEW"),
        (R.UnknownToolConfirmationRule(), {"tool": "not_a_registered_tool"}, "UNKNOWN_TOOL_REQUIRES_CONFIRMATION"),
        (R.RiskConfirmationRule(), {"risk": _risk(RiskLevel.MEDIUM, requires_confirmation=True)}, "HUMAN_CONFIRMATION_REQUIRED"),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_confirmation_rules_abstain_until_a_person_confirms_the_exact_action(engine, rule, ctx_kwargs, code):
    ctx = _ctx(engine, **ctx_kwargs)
    assert rule.apply(ctx) == DecisionStatus.ABSTAIN and ctx.reason_codes == [code]
    # sin el motivo, la regla no opina
    assert rule.apply(_ctx(engine)) is None

    engine.permission_manager.confirm_action(ctx.action.id, action_hash=ctx.action_hash)
    confirmed = _ctx(engine, **ctx_kwargs)
    assert rule.apply(confirmed) == DecisionStatus.ALLOW and confirmed.reason_codes == ["HUMAN_CONFIRMED_ACTION"]


def test_a_confirmation_does_not_carry_over_to_a_different_action_with_the_same_id(engine):
    rule, op = R.ExternalEffectConfirmationRule(), _op(CommandCategory.NETWORK)
    confirmed = _ctx(engine, action=_action("curl https://example.com"), op=op)
    engine.permission_manager.confirm_action(confirmed.action.id, action_hash=confirmed.action_hash)
    other = _ctx(engine, action=_action("curl https://evil.example"), op=op)
    assert rule.apply(other) == DecisionStatus.ABSTAIN


def test_authorize(engine):
    ctx = _ctx(engine)
    assert R.AuthorizeRule().apply(ctx) == DecisionStatus.ALLOW and ctx.reason_codes == ["GROUNDED_LOW_RISK_AUTHORIZED"]
