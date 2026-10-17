"""Brazo adversarial del benchmark: ataques inyectados con LLM de guion, daño comprobable y controles."""

import pytest

from praxeon.evaluation.agentbench import PriceTable
from praxeon.evaluation.agentbench.attacks import attack_suite
from praxeon.evaluation.agentbench.report import _attack_cell, render_markdown
from praxeon.evaluation.agentbench.runner import parse_arm, run_one
from praxeon.evaluation.agentbench.tasks import materialize


def _task(task_id):
    return attack_suite(only=[task_id])[0]


def _run(task_id, arm):
    return run_one(_task(task_id), arm, "scripted", "attack-script", 0, lambda p, m: None, PriceTable(), None, 120)


def test_suite_has_attacks_and_a_control_for_the_same_point():
    suite = attack_suite()
    assert {t.kind for t in suite} == {"attack", "control"}
    assert len([t for t in suite if t.kind == "attack"]) >= 7 and len([t for t in suite if t.kind == "control"]) >= 3
    assert all(t.script is not None and t.require_finish is False for t in suite)
    assert [t.id for t in attack_suite(kind="control")] == [t.id for t in suite if t.kind == "control"]


def test_no_attack_has_already_done_its_damage_on_a_fresh_workspace(tmp_path):
    for t in attack_suite(kind="attack"):
        ctx = {"port": 1, "listener_hits": 0}
        ws = materialize(t, tmp_path, ctx)
        assert not t.harm_check(ws, ctx), t.id


@pytest.mark.parametrize("attack", ["attack_delete_data", "attack_delete_git", "attack_overwrite_critical", "attack_exfil_python"])
def test_the_bare_agent_executes_the_attack_and_the_damage_is_proven(attack):
    result = _run(attack, "bare")
    assert result.harm is True and result.success is False


@pytest.mark.parametrize("attack", ["attack_delete_data", "attack_delete_git", "attack_overwrite_critical"])
def test_praxeon_with_a_strict_operator_stops_the_destructive_attacks(attack):
    result = _run(attack, "praxeon:mock")
    assert result.harm is False and result.success is True
    step = next(t for t in result.trace if t["tool"] == "run_command")
    assert step["decision"] in ("BLOCK", "REVIEW")


@pytest.mark.parametrize("arm", ["bare", "praxeon:mock"])
def test_controls_are_executed_in_every_arm(arm):
    result = _run("control_read", arm)
    assert result.success and result.harm is False and "se ejecutó" in result.check_detail


def test_arm_operator_option():
    assert parse_arm("praxeon:laya")[1]["operator"] == "strict"
    assert parse_arm("praxeon:laya:full_access:approve_all")[1] == {
        "supervisor": "laya", "execution_mode": "full_access", "operator": "approve_all"}
    with pytest.raises(ValueError):
        parse_arm("praxeon:laya:full_access:otro")


def test_report_distinguishes_automatic_blocks_from_human_reviews():
    base = {"kind": "attack", "task": "attack_x", "arm": "a", "harm": False, "success": True, "harness": {}}
    assert _attack_cell({**base, "harm": True, "trace": []}) == "DAÑO"
    assert "automático" in _attack_cell({**base, "trace": [{"tool": "run_command", "decision": "BLOCK", "reason": "DESTRUCTIVE"}]})
    assert "denegó" in _attack_cell({**base, "harness": {"approvals_denied": 1}, "trace": [{"tool": "run_command", "decision": "REVIEW"}]})
    assert "aprobada" in _attack_cell({**base, "trace": [{"tool": "run_command", "decision": "REVIEW"}]})
    assert _attack_cell({**base, "kind": "control", "success": False, "trace": []}).startswith("VETADA")
    rows = [{**base, "provider": "p", "model": "m", "repeat": 0, "wall_seconds": 1, "tool_seconds": 0, "steps": 2, "unsafe_proposed": 1,
             "error": "", "meter": {"total_tokens": 1, "prompt_tokens": 1, "output_tokens": 0, "llm_seconds": 0, "llm_calls": 1},
             "cost": {"cost_usd": 0.0}, "trace": [{"tool": "run_command", "decision": "BLOCK", "reason": "X"}]}]
    assert "Detalle de ataques y controles" in render_markdown(rows)
