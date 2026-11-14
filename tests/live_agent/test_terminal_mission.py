"""`praxeon live` sobre el runtime y el bucle canónico (auditoría A-02): misma política, sandbox y recibos que la app."""

from unittest.mock import patch

import pytest

from praxeon.agent_llm import SimulatedAgentLLM
from praxeon.config import default_config
from praxeon.core.session_context import SessionContextManager
from praxeon.live_agent import mission_runner
from praxeon.live_agent.runtime_session import TerminalMission
from tests.test_full_access_read_approvals import ScriptedLLM

READ_AND_FINISH = [
    'Thought: miro\nAction: list_dir({"path": "."})',
    'Thought: leo\nAction: read_file({"path": "app.py"})',
    'Thought: fin\nAction: finish({"summary": "app.py imprime 1."})',
]
PUSH = 'Thought: subo\nAction: run_command({"command": "git push origin main"})'


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("print(1)\n", encoding="utf-8")
    return ws


@pytest.fixture
def mission(tmp_path):
    return TerminalMission(db_dir=str(tmp_path / "db"))


def _run(mission, workspace, turns, **kw):
    kw.setdefault("provider", "ollama")
    kw.setdefault("supervisor", "mock")
    kw.setdefault("max_steps", 8)
    llm = ScriptedLLM(turns) if turns is not None else SimulatedAgentLLM()
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        return mission.run("¿Qué hace app.py?", workspace_root=str(workspace), **kw)


def _mock_supervisor():
    config = default_config.model_copy()
    config.supervisor = "mock"
    return config


def test_a_supervisor_cut_is_not_reported_as_a_finished_task(mission, workspace):
    looping = ['Thought: leo\nAction: read_file({"path": "ghost.py"})'] * 12
    result = _run(mission, workspace, looping, max_steps=10)
    assert not result.finished


def test_typesafe_without_an_explicit_model_uses_a_real_model_name():
    from praxeon.domain.decision_provider import DecisionModelConfig
    from praxeon.providers.registry import TYPESAFE_DEFAULT_MODEL, _create_typesafe_provider

    for model_id in ("typesafe", "default"):
        provider = _create_typesafe_provider(DecisionModelConfig(provider="typesafe", model_id=model_id))
        assert provider.model_name == TYPESAFE_DEFAULT_MODEL
    assert _create_typesafe_provider(DecisionModelConfig(provider="typesafe", model_id="jev-latest")).model_name == "jev-latest"


def test_a_task_runs_through_the_runtime_policy_and_ends_with_the_answer(mission, workspace):
    result = _run(mission, workspace, READ_AND_FINISH)
    assert result.finished and result.final_answer == "app.py imprime 1."
    assert [(s["tool"], s["verdict"]) for s in result.steps] == [("list_dir", "ALLOW"), ("read_file", "ALLOW"), ("finish", "ALLOW")]
    assert "print(1)" in result.steps[1]["observation"]
    # es una misión del runtime: queda registrada con sus decisiones, como las de la app
    assert mission.service.state_store.load_state(result.session_id) is not None


@pytest.mark.parametrize("approved", [True, False])
def test_an_action_that_needs_confirmation_is_asked_in_the_terminal(tmp_path, workspace, approved):
    asked = []
    mission = TerminalMission(db_dir=str(tmp_path / "db"), approver=lambda event: asked.append(event.decision_id) or approved)
    result = _run(mission, workspace, [PUSH, READ_AND_FINISH[-1]])
    assert len(asked) == 1
    push = result.steps[0]
    assert push["tool"] == "run_command" and push["verdict"] == "REVIEW"
    # aprobada se ejecuta (deja observación); rechazada, no
    assert ("observation" in push) is approved


def test_without_a_way_to_ask_nothing_that_needs_confirmation_is_approved(mission, workspace):
    result = _run(mission, workspace, [PUSH, READ_AND_FINISH[-1]])
    assert "observation" not in result.steps[0]


def test_fail_closed_stops_when_the_model_is_not_available(mission, workspace):
    result = _run(mission, workspace, None, provider="gemini", llm_failure_policy="fail_closed")
    assert not result.finished and result.steps == []
    assert "fail_closed" in result.fatal_error


def test_best_effort_falls_back_to_the_planner_and_marks_its_steps_as_synthetic(mission, workspace):
    result = _run(mission, workspace, None, provider="gemini", max_steps=2)
    assert result.degraded and result.steps
    assert all(s["synthetic_fallback"] and s["model_source"] == "synthetic:fallback" for s in result.steps)


def test_run_live_agent_raises_in_fail_closed(mission, workspace, monkeypatch):
    monkeypatch.chdir(workspace)
    with patch("praxeon.agent_llm.create_agent_llm", return_value=SimulatedAgentLLM()):
        with pytest.raises(RuntimeError, match="FAIL_CLOSED"):
            mission_runner.run_live_agent("tarea", provider="gemini", fallback_mode="FAIL_CLOSED", mission=mission, config=_mock_supervisor())


def test_run_live_agent_keeps_the_memory_between_chained_tasks(mission, workspace, monkeypatch):
    monkeypatch.chdir(workspace)
    seen_histories = []
    real_run = mission.run

    def spy(goal, **kw):
        seen_histories.append(list(kw.get("chat_history") or []))
        return real_run(goal, **kw)

    monkeypatch.setattr(mission, "run", spy)
    session = SessionContextManager()
    config = default_config.model_copy()
    config.supervisor = "mock"   # sin red: el supervisor por defecto es TypeSafe
    with patch("praxeon.agent_llm.create_agent_llm", side_effect=[ScriptedLLM(READ_AND_FINISH), ScriptedLLM(READ_AND_FINISH)]):
        finished, answer, session, _ = mission_runner.run_live_agent(
            "primera", provider="ollama", session_context=session, mission=mission, fallback_mode="BEST_EFFORT", config=config
        )
        mission_runner.run_live_agent(
            "segunda", provider="ollama", session_context=session, mission=mission, fallback_mode="BEST_EFFORT", config=config
        )
    assert finished and answer == "app.py imprime 1."
    assert seen_histories[0] == []
    assert seen_histories[1] == [{"role": "user", "content": "primera"}, {"role": "assistant", "content": "app.py imprime 1."}]
    assert [r.goal for r in session.task_records] == ["primera", "segunda"]
    assert session.task_records[0].history_steps[0]["governance_mode"] == "BEST_EFFORT"


def test_the_live_dashboard_paints_the_runtime_events(tmp_path, workspace, monkeypatch):
    from praxeon import dashboard

    monkeypatch.setenv("PRAXEON_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.chdir(workspace)
    with patch("praxeon.agent_llm.create_agent_llm", return_value=ScriptedLLM(READ_AND_FINISH)):
        finished, summary, dash, turns, session, _ = dashboard._execute_single_live_task(
            "¿Qué hace app.py?", provider="ollama", config=_mock_supervisor(), max_steps=6
        )
    assert finished and summary == "app.py imprime 1." and turns == 3
    assert dash.executed_steps == 3 and dash.interventions_count == 0
    assert [h["score_txt"] for h in dash.step_history] == ["ALLOW", "ALLOW", "ALLOW"]
    assert session.task_records[0].final_answer == "app.py imprime 1."
