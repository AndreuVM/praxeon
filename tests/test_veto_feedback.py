"""El agente recibe una explicación útil cuando el supervisor veta una acción."""

import pytest

from praxeon.server.runtime_service.veto_feedback import explain_veto


@pytest.mark.parametrize("codes,fragment", [
    (["SUPERVISOR_LAYA_DESTRUCTIVE_BLOCK"], "destructiva"),
    (["CRITICAL_OPERATIONAL_RISK"], "destructiva"),
    (["HIGH_LOOP_PROBABILITY (0.88 >= 0.65)"], "ya hiciste"),
    (["LOW_GROUNDED_PROBABILITY (0.05 < 0.35)", "PREMATURE_COMPLETION_WITHOUT_EVIDENCE"], "sin haber consultado"),
    (["LOW_GROUNDED_PROBABILITY (0.05 < 0.35)"], "no se apoya"),
    (["SUPERVISOR_LAYA_UNNECESSARY_ACTION_REPLAN"], "innecesaria"),
])
def test_each_reason_is_explained_with_what_to_do(codes, fragment):
    msg = explain_veto("BLOCK", codes, "run_command", {"command": "rm -rf data"})
    assert "NO se ha ejecutado" in msg and "run_command" in msg and "rm -rf data" in msg and fragment in msg


def test_the_message_does_not_reuse_the_supervisors_internal_vocabulary():
    msg = explain_veto("BLOCK", ["SUPERVISOR_LAYA_DESTRUCTIVE_BLOCK"], "run_command", {"command": "x"})
    for word in ("LAYA", "SUPERVISOR_", "JEV", "PELIGROSA"):
        assert word not in msg


def test_unknown_reasons_still_tell_the_agent_not_to_repeat_the_action():
    assert "No repitas" in explain_veto("REPLAN", ["OTRA_COSA"], "read_file", {"path": "a"})


def test_long_arguments_are_shortened():
    msg = explain_veto("BLOCK", ["DESTRUCTIVE"], "write_file", {"path": "a", "content": "x" * 5000})
    assert len(msg) < 700
