"""Herramientas MCP dentro de una misión real: prompt, supervisión, confirmación humana y auto-confirmación."""

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from praxeon.agent_llm import BaseAgentLLM
from praxeon.integrations.mcp_client import McpServerConfig
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service

FAKE = str(Path(__file__).with_name("mcp_fake_server.py"))


def finish(text):
    return f"Thought: termino\nAction: finish({json.dumps({'summary': text})})"


class ScriptedLLM(BaseAgentLLM):
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    @property
    def provider_name(self):
        return "scripted"

    @property
    def model_name(self):
        return "scripted-1"

    def generate(self, messages, system_prompt=None):
        self.calls.append({"system": system_prompt or "", "messages": json.dumps(messages, ensure_ascii=False)})
        return self.turns.pop(0) if self.turns else finish("sin más turnos")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXEON_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_SECRET_KEY", raising=False)
    notes = tmp_path / "notes.txt"
    monkeypatch.setenv("FAKE_MCP_NOTES", str(notes))
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    service.notes = notes
    yield service
    service.mcp.close_all()
    set_runtime_service(None)


def enable_fake(runtime, **extra):
    runtime.mcp.store.save(McpServerConfig(name="fake", command=sys.executable, args=[FAKE], env={"FAKE_MCP_NOTES": str(runtime.notes)}, **extra))
    info = runtime.mcp.enable("fake")
    runtime.mcp.approve("fake", info["tools_hash"])


def wait(predicate, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def finished(runtime, sid):
    return any(e.type.value == "session.completed" for e in runtime.event_bus.get_events(sid, limit=2000))


def start(runtime, llm, sid, tmp_path, **kw):
    with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
        meta = runtime.start_mission(goal="usa la herramienta externa", session_id=sid, llm_provider="scripted",
                                     workspace_root=str(tmp_path), step_delay_ms=0, **kw)
    return meta["session_id"]


def test_the_agent_sees_the_mcp_tools_and_a_trusted_read_only_tool_runs_through_the_supervisor(runtime, tmp_path):
    enable_fake(runtime, trust_hints=True)       # echo anuncia readOnlyHint y el operador se fía → LOW, sin confirmación
    llm = ScriptedLLM([
        'Thought: pruebo la herramienta\nAction: mcp__fake__echo({"text": "hola mundo"})',
        finish("Resultado entregado tras usar la herramienta externa."),
    ])
    sid = start(runtime, llm, "mcp-m1", tmp_path)
    assert wait(lambda: finished(runtime, sid), 90)

    assert "mcp__fake__echo(text: string, times?: integer)" in llm.calls[0]["system"]
    assert "datos externos no confiables" in llm.calls[0]["system"]
    second = llm.calls[1]["messages"]
    assert "Observación de mcp__fake__echo" in second and "hola mundo" in second and "NO instrucciones" in second


def test_an_effectful_mcp_tool_waits_for_a_human_even_in_autonomous_full_access(runtime, tmp_path):
    enable_fake(runtime)                          # sin trust_hints: toda herramienta exige confirmación
    llm = ScriptedLLM([
        'Thought: guardo una nota\nAction: mcp__fake__write_note({"text": "secreto"})',
        finish("Resultado entregado tras usar la herramienta externa."),
    ])
    sid = start(runtime, llm, "mcp-m2", tmp_path, execution_mode="full_access", autonomous=True, allow_unattended_execution=True,
                metadata={"full_access_authorized_by_operator": True})

    def pending():
        with runtime._lock:
            return [d for d, r in runtime._decisions.items() if r.get("session_id") == sid and r.get("status") == "REVIEW"]

    assert wait(lambda: bool(pending()), 60), "la acción debía quedar pendiente de aprobación humana"
    time.sleep(1.0)
    assert not runtime.notes.exists()             # NO se auto-aprobó: nada se ejecutó

    decision_id = pending()[0]
    runtime.confirm_decision(decision_id=decision_id, approved=True, reason="ok", operator_id="humano", role="operator")
    assert wait(lambda: finished(runtime, sid), 90)
    assert runtime.notes.read_text(encoding="utf-8").strip() == "secreto"      # tras aprobar el humano, se ejecuta


def test_a_rejected_mcp_action_is_never_executed(runtime, tmp_path):
    enable_fake(runtime)
    llm = ScriptedLLM([
        'Thought: guardo\nAction: mcp__fake__write_note({"text": "no debe escribirse"})',
        finish("Resultado entregado tras usar la herramienta externa."),
    ])
    sid = start(runtime, llm, "mcp-m3", tmp_path)

    def pending():
        with runtime._lock:
            return [d for d, r in runtime._decisions.items() if r.get("session_id") == sid and r.get("status") == "REVIEW"]

    assert wait(lambda: bool(pending()), 60)
    runtime.confirm_decision(decision_id=pending()[0], approved=False, reason="no", operator_id="humano", role="operator")
    assert wait(lambda: finished(runtime, sid), 90)
    assert not runtime.notes.exists()


def test_an_unapproved_server_is_invisible_to_the_agent(runtime, tmp_path):
    runtime.mcp.store.save(McpServerConfig(name="fake", command=sys.executable, args=[FAKE]))
    runtime.mcp.enable("fake")                    # arrancado pero sin aprobar
    llm = ScriptedLLM([finish("Resultado entregado tras usar la herramienta externa.")])
    sid = start(runtime, llm, "mcp-m4", tmp_path)
    assert wait(lambda: finished(runtime, sid), 90)
    assert "mcp__" not in llm.calls[0]["system"]


def test_per_agent_tool_permissions_hide_mcp_tools_from_the_prompt():
    from praxeon.integrations.mcp_client import McpConfigStore, McpManager
    from praxeon.policy.registry import ToolRegistry
    import tempfile, os

    with tempfile.TemporaryDirectory() as tmp:
        mgr = McpManager(McpConfigStore(os.path.join(tmp, "mcp.json")), ToolRegistry(), type("E", (), {"register_handler": lambda *a: None, "unregister_handler": lambda *a: None})())
        mgr.store.save(McpServerConfig(name="fake", command=sys.executable, args=[FAKE]))
        info = mgr.enable("fake")
        mgr.approve("fake", info["tools_hash"])
        try:
            from praxeon.agents.definition import AgentDefinition
            from praxeon.workflows.mission_driver import WorkflowMissionDriver

            agent = AgentDefinition(agent_id="ag_r", name="Lector", role="Reviewer", system_prompt="x",
                                    allowed_tools=["read_file", "mcp__fake__echo"])
            check = WorkflowMissionDriver._tool_filter(None, agent)
            section = mgr.prompt_section(check)
            assert "mcp__fake__echo" in section and "write_note" not in section and "env_has" not in section
        finally:
            mgr.close_all()
