"""El extractor reconstruye el estado de cada acción real, descarta las guionizadas y redacta datos personales."""

import json
import sqlite3
from pathlib import Path

from praxeon.evaluation.finetune.real_sessions import extract_db, is_real_llm


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "events.db"
    conn = sqlite3.connect(path)
    conn.execute("create table runtime_events (event_id text primary key, session_id text, sequence integer, type text, timestamp text,"
                 " node_id text, parent_id text, decision_id text, payload text)")
    home = str(Path.home())
    rows = [
        ("session.started", "root", None, None, {"execution_mode": "full_access"}),
        ("goal.created", "g", "root", None, {"goal": "Resumir el README"}),
        ("action.proposed", "a1", "root", "d1", {"tool": "read_file", "arguments": {"path": "README.md"}, "source": "LLM (OLLAMA - qwen)", "step": 1}),
        ("operation.classified", "a1", None, "d1", {"category": "inspection"}),
        ("policy.decided", "a1_policy", "a1", "d1", {"status": "ALLOW", "reason_code": "OK"}),
        ("execution.completed", "a1", None, "d1", {"success": True}),
        ("observation.recorded", "a1", None, "d1", {"output": f"leído en {home}\\README.md api_key=supersecret99"}),
        ("action.proposed", "a2", "a1", "d2", {"tool": "run_command", "arguments": {"command": "rm -rf build"}, "source": "LLM (OLLAMA - qwen)", "step": 2}),
        ("policy.decided", "a2_policy", "a2", "d2", {"status": "REVIEW", "reason_code": "WORKSPACE_DELETE_REQUIRES_REVIEW"}),
        ("approval.completed", "a2", None, "d2", {"approved": False}),
        ("action.proposed", "x1", "root", "d3", {"tool": "run_command", "arguments": {"command": "sudo reboot"}, "source": "ExternalAgent", "step": 1}),
    ]
    for i, (t, node, parent, dec, payload) in enumerate(rows, start=1):
        conn.execute("insert into runtime_events values (?,?,?,?,?,?,?,?,?)", (f"e{i}", "s1", i, t, "", node, parent, dec, json.dumps(payload)))
    conn.commit()
    conn.close()
    return path


def test_real_sources_are_recognised():
    assert is_real_llm("LLM (CLAUDE_CLI - sonnet)") and is_real_llm("LLM (GEMINI)")
    assert not is_real_llm("ExternalAgent") and not is_real_llm("LLM (SIMULATOR)") and not is_real_llm(None)


def test_each_real_action_becomes_a_case_with_state_history_and_human_outcome(tmp_path):
    cases = list(extract_db(_db(tmp_path)))
    assert [c["tool"] for c in cases] == ["read_file", "run_command"]          # la acción guionizada no entra
    second = cases[1]
    assert "OBJETIVO: Resumir el README" in second["state"] and "PASO 1" in second["state"] and "rm -rf build" in second["state"]
    assert second["observed"]["policy_status"] == "REVIEW" and second["observed"]["approved_by_human"] is False
    assert second["classifier"]["rules"] and "RULE_WORKSPACE_DELETE_REVIEW" in second["classifier"]["rules"]
    assert second["history_len"] == 1 and cases[0]["observed"]["success"] is True


def test_personal_data_and_secrets_are_redacted(tmp_path):
    blob = json.dumps(list(extract_db(_db(tmp_path))))
    assert str(Path.home()) not in blob and "supersecret99" not in blob


def test_scripted_agents_can_be_included_on_request(tmp_path):
    assert len(list(extract_db(_db(tmp_path), only_real=False))) == 3
