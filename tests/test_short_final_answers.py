"""Una respuesta final corta ("8123") es legítima si el agente ya reunió evidencias; sin ellas sigue siendo evasiva."""

import time
from unittest.mock import patch

import pytest

from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
from tests.test_full_access_read_approvals import ScriptedLLM


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_SECRET_KEY", raising=False)
    monkeypatch.setenv("PRAXEON_CACHE_DIR", str(tmp_path / "cache"))
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


def _run(runtime, tmp_path, turns, sid):
    (tmp_path / "prod.toml").write_text("port = 8123\nworkers = 4\n", encoding="utf-8")
    with patch("praxeon.agent_llm.create_agent_llm", return_value=ScriptedLLM(turns)):
        runtime.start_mission(
            goal="¿Qué puerto usa producción?", session_id=sid, llm_provider="scripted", supervisor="mock",
            workspace_root=str(tmp_path), max_steps=6, step_delay_ms=0, execution_mode="local_restricted",
            autonomous=True, allow_unattended_execution=True,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            events = runtime.event_bus.get_all_events(sid)
            if any(e.type.value == "session.completed" for e in events):
                break
            time.sleep(0.2)
    events = runtime.event_bus.get_all_events(sid)
    statuses = [str(e.payload.get("status", "")).upper() for e in events if e.type.value == "policy.decided"]
    done = next(e for e in events if e.type.value == "session.completed")
    return statuses, done.payload


def test_a_short_answer_after_reading_evidence_is_accepted(runtime, tmp_path):
    turns = ['Thought: miro\nAction: list_dir({"path": "."})', 'Thought: leo\nAction: read_file({"path": "prod.toml"})',
             'Thought: fin\nAction: finish({"summary": "8123"})']
    statuses, done = _run(runtime, tmp_path, turns, "short-ok")
    assert statuses == ["ALLOW", "ALLOW", "ALLOW"] and done["summary"] == "8123"


def test_a_short_answer_without_any_evidence_is_still_vetoed(runtime, tmp_path):
    turns = ['Thought: fin\nAction: finish({"summary": "8123"})'] * 8
    statuses, done = _run(runtime, tmp_path, turns, "short-no-evidence")
    assert "REPLAN" in statuses and done["summary"] != "8123"
