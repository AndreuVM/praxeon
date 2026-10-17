"""Un error recuperable no debe cortar la misión: ruta inexistente, repeticiones tras un cambio y estancamiento real."""

import time
from unittest.mock import patch

import pytest

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.providers.context import ProviderContext
from praxeon.providers.laya import LayaProvider
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
from tests.test_full_access_read_approvals import ScriptedLLM


def _loop(provider, tool, args, history):
    ctx = ProviderContext(
        session_id="s", goal="Reparar el bug", history_window=history,
        candidate_action={"id": "c", "tool_name": tool, "arguments": args, "description": "x"},
    )
    return provider._infer_primitives_calibrated(ctx).noul["is_loop"]


def _h(tool, args, obs="ok"):
    return {"id": "h", "tool": tool, "arguments": args, "observation": obs}


@pytest.fixture
def laya():
    return LayaProvider(backend="simulated")


def test_one_repeat_is_a_reasonable_retry_and_the_second_is_a_loop(laya):
    read = ("read_file", {"path": "a.py"})
    assert _loop(laya, *read, []) < 0.1
    assert _loop(laya, *read, [_h(*read)]) < 0.65                      # una repetición aislada no veta
    assert _loop(laya, *read, [_h(*read), _h(*read)]) >= 0.65          # la segunda sí


def test_rereading_or_rerunning_tests_after_an_edit_is_not_a_loop(laya):
    read, tests = ("read_file", {"path": "a.py"}), ("run_command", {"command": "pytest -q"})
    edit = ("edit_file", {"path": "a.py", "content": "x = 2"})
    assert _loop(laya, *read, [_h(*read), _h(*read), _h(*edit)]) < 0.1
    assert _loop(laya, *tests, [_h(*tests, "1 failed"), _h(*tests, "1 failed"), _h(*edit), _h(*tests, "1 passed")]) < 0.65


def test_failing_again_without_changing_anything_is_stagnation(laya):
    tests = ("run_command", {"command": "pytest -q"})
    failing = [_h(*tests, "Error: 1 failed"), _h(*tests, "Error: 1 failed")]
    assert _loop(laya, *tests, failing) >= 0.85


def test_a_failure_before_an_edit_does_not_count_as_stagnation(laya):
    tests, edit = ("run_command", {"command": "pytest -q"}), ("edit_file", {"path": "a.py", "content": "y"})
    history = [_h(*tests, "Error: 1 failed"), _h(*tests, "Error: 1 failed"), _h(*edit)]
    assert _loop(laya, "read_file", {"path": "a.py"}, history) < 0.65


def test_the_agents_own_words_do_not_decide_a_loop(laya):
    ctx = ProviderContext(
        session_id="s", goal="g", history_window=[],
        candidate_action={"id": "c", "tool_name": "read_file", "arguments": {"path": "a.py"},
                          "description": "Voy a reintentar y repetir el mismo comando, un bucle"},
    )
    assert laya._infer_primitives_calibrated(ctx).noul["is_loop"] < 0.1


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_SECRET_KEY", raising=False)
    monkeypatch.setenv("PRAXEON_CACHE_DIR", str(tmp_path / "cache"))
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


def _statuses(runtime, tmp_path, turns, sid):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "prod.toml").write_text("port = 8123\nworkers = 4\n", encoding="utf-8")
    with patch("praxeon.agent_llm.create_agent_llm", return_value=ScriptedLLM(turns)):
        runtime.start_mission(
            goal="¿Qué puerto usa producción?", session_id=sid, llm_provider="scripted", supervisor="mock",
            workspace_root=str(tmp_path), max_steps=14, step_delay_ms=0, execution_mode="local_restricted",
            autonomous=True, allow_unattended_execution=True,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            if any(e.type.value == "session.completed" for e in runtime.event_bus.get_all_events(sid)):
                break
            time.sleep(0.2)
    events = runtime.event_bus.get_all_events(sid)
    rows = [(e.payload.get("status", "").upper(), e.payload.get("reason_code", "")) for e in events if e.type.value == "policy.decided"]
    return rows, next(e for e in events if e.type.value == "session.completed").payload


def test_a_wrong_path_is_corrected_from_the_tool_error_instead_of_being_vetoed(runtime, tmp_path):
    turns = ['Thought: miro\nAction: list_dir({"path": "."})', 'Thought: leo\nAction: read_file({"path": "prod.toml"})',
             'Thought: está en config\nAction: read_file({"path": "config/prod.toml"})',
             'Thought: fin\nAction: finish({"summary": "El puerto de produccion es 8123"})']
    rows, done = _statuses(runtime, tmp_path, turns, "wrong-path")
    assert [s for s, _ in rows] == ["ALLOW", "ALLOW", "ALLOW", "ALLOW"]
    assert "8123" in done["summary"]


def test_the_error_for_a_missing_path_lists_what_really_exists(runtime, tmp_path):
    seen = []

    class Recording(ScriptedLLM):
        def generate(self, messages, system_prompt=None):
            seen.append(" ".join(str(m.get("content", "")) for m in messages))
            return super().generate(messages, system_prompt)

    turns = ['Thought: miro\nAction: list_dir({"path": "."})', 'Thought: leo\nAction: read_file({"path": "prod.toml"})',
             'Thought: fin\nAction: finish({"summary": "no lo encuentro"})']
    (tmp_path / "config").mkdir()
    with patch("praxeon.agent_llm.create_agent_llm", return_value=Recording(turns)):
        runtime.start_mission(goal="p", session_id="feedback", llm_provider="scripted", supervisor="mock",
                              workspace_root=str(tmp_path), max_steps=6, step_delay_ms=0, execution_mode="local_restricted",
                              autonomous=True, allow_unattended_execution=True)
        deadline = time.time() + 60
        while time.time() < deadline and not any(e.type.value == "session.completed" for e in runtime.event_bus.get_all_events("feedback")):
            time.sleep(0.2)
    last = seen[-1]
    assert "NO EXISTE" in last and "config" in last      # el modelo ve qué hay de verdad en el workspace


def test_insisting_on_the_same_missing_path_is_still_vetoed(runtime, tmp_path):
    turns = ['Thought: miro\nAction: list_dir({"path": "."})'] + ['Thought: leo\nAction: read_file({"path": "prod.toml"})'] * 3
    rows, _ = _statuses(runtime, tmp_path, turns, "same-path")
    statuses = [s for s, _ in rows]
    assert statuses[:2] == ["ALLOW", "ALLOW"] and "REPLAN" in statuses[2:]


def test_the_supervisor_cuts_a_mission_only_after_six_consecutive_failures(runtime, tmp_path):
    from praxeon.server.runtime_service.mission_runtime import MAX_CONSECUTIVE_FAILURES

    assert MAX_CONSECUTIVE_FAILURES == 6
    turns = ['Thought: miro\nAction: list_dir({"path": "."})'] + ['Thought: leo\nAction: read_file({"path": "prod.toml"})'] * 12
    rows, done = _statuses(runtime, tmp_path, turns, "cut-late")
    assert len(rows) >= 7 and "concluida preventivamente" in done["summary"]   # list_dir + al menos 6 intentos fallidos
