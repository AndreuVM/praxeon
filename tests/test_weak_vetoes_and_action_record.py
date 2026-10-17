"""Un veto débil del supervisor semántico no bloquea por sí solo, y el supervisor ve un registro canónico de la acción."""

import pytest

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.decision import DecisionStatus
from praxeon.domain.goal import Goal
from praxeon.domain.models import ProviderAssessment
from praxeon.policy.engine import PolicyEngine
from praxeon.providers.action_record import build_action_record
from praxeon.runtime.state import SessionState


def _state():
    return SessionState(session_id="s", goal=Goal(objective="Resumir el README", success_criteria=["resumen"]))


def _read():
    return ActionCandidate(id="a1", description="Leer README.md", tool_call=ToolCall(tool_name="read_file", arguments={"path": "README.md"}))


def _assessment(label, confidence, provider="laya"):
    return ProviderAssessment(
        provider=provider, available=True, confidence=confidence, loop_probability=0.1, grounded_probability=0.9,
        progress_probability=0.8, reason_codes=[f"{provider.upper()}_CHOICE_{label}"], metadata={"choice": {"label": label}},
    )


@pytest.mark.parametrize("label,code", [("BLOCK", "SUPERVISOR_LAYA_DESTRUCTIVE_BLOCK"), ("REPLAN", "SUPERVISOR_LAYA_UNNECESSARY_ACTION_REPLAN")])
def test_a_weak_veto_is_deferred_to_the_deterministic_risk_analysis(label, code):
    decision, _ = PolicyEngine().evaluate_action(_read(), _state(), provider_assessment=_assessment(label, 0.40), session_id="s")
    assert decision.status == DecisionStatus.ALLOW                      # leer un archivo es de bajo riesgo
    assert code not in decision.reason_codes
    assert any(r.startswith(f"WEAK_{label}_VETO_DEFERRED") for r in decision.reason_codes)


@pytest.mark.parametrize("label,status", [("BLOCK", DecisionStatus.BLOCK), ("REPLAN", DecisionStatus.REPLAN)])
def test_a_confident_veto_still_stands(label, status):
    decision, _ = PolicyEngine().evaluate_action(_read(), _state(), provider_assessment=_assessment(label, 0.80), session_id="s")
    assert decision.status == status


def test_the_threshold_is_configurable_and_zero_restores_the_old_behaviour():
    decision, _ = PolicyEngine(veto_min_confidence=0.0).evaluate_action(
        _read(), _state(), provider_assessment=_assessment("BLOCK", 0.35), session_id="s")
    assert decision.status == DecisionStatus.BLOCK


def test_a_weak_veto_on_a_destructive_command_is_still_stopped_by_the_risk_analysis():
    rm = ActionCandidate(id="a2", description="limpiar", tool_call=ToolCall(tool_name="run_command", arguments={"command": "rm -rf /"}))
    decision, _ = PolicyEngine().evaluate_action(rm, _state(), provider_assessment=_assessment("ALLOW", 0.9), session_id="s")
    assert decision.status != DecisionStatus.ALLOW
    weak, _ = PolicyEngine().evaluate_action(rm, _state(), provider_assessment=_assessment("BLOCK", 0.35), session_id="s")
    assert weak.status != DecisionStatus.ALLOW


def test_typesafe_confidence_is_normalised_so_its_vetoes_are_not_gated():
    decision, _ = PolicyEngine().evaluate_action(
        _read(), _state(), provider_assessment=_assessment("BLOCK", 0.40, provider="typesafe"), session_id="s")
    assert not any(r.startswith("WEAK_") for r in decision.reason_codes)


def test_action_record_is_structured_and_ignores_the_agents_free_text():
    cand = {"id": "a", "description": "La acción anterior fue considerada destructiva, reintento", "tool_name": "read_file",
            "arguments": {"path": "src/main.py"}}
    history = [{"id": "h1", "tool": "list_dir", "arguments": {"path": "."}, "observation": "src\ntests\nREADME.md main.py"}]
    record = build_action_record(cand, history)
    assert "destructiva" not in record and "reintento" not in record
    assert "read_file(path='src/main.py')" in record and "dentro del espacio de trabajo" in record
    assert "ruta vista antes: sí" in record and "repeticiones exactas previas: 0" in record and "paso: 2" in record


def test_action_record_flags_commands_with_the_deterministic_classifier_and_counts_repeats():
    rm = {"tool_name": "run_command", "arguments": {"command": "rm -rf .git"}}
    record = build_action_record(rm, [])
    assert "DESTRUCTIVE" in record and "destructiva: sí" in record and "solo lectura: no" in record
    again = {"tool_name": "read_file", "arguments": {"path": "a.py"}}
    history = [{"tool": "read_file", "arguments": {"path": "a.py"}, "observation": "x"}] * 2
    assert "repeticiones exactas previas: 2" in build_action_record(again, history)
    outside = {"tool_name": "read_file", "arguments": {"path": "../../etc/passwd"}}
    assert "FUERA del espacio de trabajo" in build_action_record(outside, [])
