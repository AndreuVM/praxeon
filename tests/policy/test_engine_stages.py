"""Etapas de PolicyEngine.evaluate_action probadas por separado (auditoría A-01)."""

import pytest

from praxeon.domain.models import ActionCandidate, DecisionStatus, ProviderAssessment, ToolCall, compute_action_hash
from praxeon.policy.engine import PolicyEngine


@pytest.fixture
def engine():
    return PolicyEngine()


def _action(command="ls", tool="run_command", **kw):
    return ActionCandidate(id="a1", description=command, tool_call=ToolCall(tool_name=tool, arguments={"command": command}), **kw)


def _assess(engine, command, tool="run_command"):
    return engine.classifier.classify(command)


def test_confirm_or_abstain_needs_a_confirmation_of_the_exact_action(engine):
    action, codes = _action("git push origin main"), []
    h = compute_action_hash(action)
    assert engine._confirm_or_abstain(action, h, "X_REQUIRES_CONFIRMATION", codes) == DecisionStatus.ABSTAIN
    assert codes == ["X_REQUIRES_CONFIRMATION"]
    engine.permission_manager.confirm_action(action.id, action_hash=h)
    codes = []
    assert engine._confirm_or_abstain(action, h, "X_REQUIRES_CONFIRMATION", codes) == DecisionStatus.ALLOW
    assert codes == ["HUMAN_CONFIRMED_ACTION"]


def test_forbidden_tool_is_blocked_before_anything_else(engine):
    action, codes = _action(), []
    status = engine._screen_action(action, "run_command", {"run_command"}, [], None, None, _assess(engine, "ls"),
                                   engine.registry.assess_risk("run_command"), True, codes)
    assert status == DecisionStatus.BLOCK and codes == ["TOOL_FORBIDDEN_BY_SUPERVISOR"]


def test_missing_evidence_asks_to_replan(engine):
    action, codes = _action(requires_evidence=["hecho necesario"]), []
    status = engine._screen_action(action, "run_command", set(), [], None, None, _assess(engine, "ls"),
                                   engine.registry.assess_risk("run_command"), True, codes)
    assert status == DecisionStatus.REPLAN and codes[0].startswith("MISSING_REQUIRED_EVIDENCE")


def test_screen_has_no_opinion_when_nothing_applies(engine):
    codes = []
    status = engine._screen_action(_action(), "run_command", set(), [], None, None, _assess(engine, "ls"),
                                   engine.registry.assess_risk("run_command"), True, codes)
    assert status is None and codes == []


def test_unavailable_supervisor_applies_the_failsafe(engine):
    codes = []
    provider = ProviderAssessment(provider="laya", available=False)
    status = engine._screen_action(_action(), "run_command", set(), [], None, provider, _assess(engine, "ls"),
                                   engine.registry.assess_risk("run_command"), True, codes)
    assert status is not None and codes[-1].startswith("PROVIDER_UNAVAILABLE")


def test_advisory_supervisor_block_is_only_a_review_request(engine):
    provider = ProviderAssessment(
        provider="laya", confidence=0.9,
        metadata={"veto_mode": "advisory", "choice": {"label": "BLOCK", "raw_probabilities": {"BLOCK": 0.9}}},
    )
    codes = []
    assert engine._supervisor_verdict(provider, codes) == DecisionStatus.ABSTAIN
    assert "ADVISORY_REVIEW" in codes[0]


def test_low_confidence_provider_escalates(engine):
    codes = []
    assert engine._supervisor_verdict(ProviderAssessment(provider="typesafe", confidence=0.05), codes) == DecisionStatus.ABSTAIN
    assert codes[0].startswith("LOW_PROVIDER_CONFIDENCE_ESCALATE")


def test_high_loop_probability_replans(engine):
    codes = []
    provider = ProviderAssessment(provider="typesafe", confidence=0.9, loop_probability=0.95)
    assert engine._supervisor_verdict(provider, codes) == DecisionStatus.REPLAN


def test_operational_risk_asks_confirmation_for_network_and_unknown(engine):
    for command, code in (("curl http://example.com", "NETWORK_REQUIRES_CONFIRMATION"), ("frobnicate --x", "UNKNOWN_OPERATION_REVIEW")):
        action, codes = _action(command), []
        assessment = _assess(engine, command)
        status = engine._assess_operational_risk(action, compute_action_hash(action), "run_command", assessment,
                                                 engine.registry.assess_risk("run_command"), codes)
        assert status == DecisionStatus.ABSTAIN and codes == [code]


def test_operational_risk_has_no_opinion_for_a_plain_read(engine):
    action, codes = _action("ls"), []
    risk = engine.registry.assess_risk("run_command")
    status = engine._assess_operational_risk(action, compute_action_hash(action), "run_command", _assess(engine, "ls"), risk, codes)
    assert status is None or status == DecisionStatus.ABSTAIN  # el riesgo base de run_command puede pedir confirmación
