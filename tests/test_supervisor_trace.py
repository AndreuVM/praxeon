"""El volcado del supervisor es opcional, redacta secretos y rutas personales, y nunca rompe una decisión."""

import json
from pathlib import Path

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.goal import Goal
from praxeon.providers.laya import LayaProvider
from praxeon.providers.supervisor_trace import redact, trace_path
from praxeon.runtime.state import SessionState


def _evaluate():
    state = SessionState(session_id="s", goal=Goal(objective="Resumir el README", success_criteria=["resumen"]))
    action = ActionCandidate(id="a", description="leer", tool_call=ToolCall(tool_name="read_file", arguments={"path": "README.md"}))
    return LayaProvider(backend="simulated").evaluate_action(state, action)


def test_it_is_off_by_default(monkeypatch):
    monkeypatch.delenv("PRAXEON_TRACE_SUPERVISOR", raising=False)
    assert trace_path() is None
    _evaluate()


def test_it_writes_one_row_per_decision_with_the_state_and_the_label(monkeypatch, tmp_path):
    out = tmp_path / "t.jsonl"
    monkeypatch.setenv("PRAXEON_TRACE_SUPERVISOR", str(out))
    monkeypatch.setenv("PRAXEON_TRACE_LABEL", "p|m|laya|t1|0")
    _evaluate()
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["label"] == "p|m|laya|t1|0" and row["tool"] == "read_file" and row["state"] and row["questions"]
    assert row["choice"]["label"] in ("ALLOW", "REPLAN", "BLOCK", "ABSTAIN")


def test_secrets_and_the_home_directory_are_redacted(monkeypatch):
    home = str(Path.home())
    text = f"leer {home}\\x api_key=abcd1234efgh5678 token: ghp_{'a' * 30} sk-{'b' * 24} AKIA{'C' * 16}"
    cleaned = redact(text)
    assert "abcd1234efgh5678" not in cleaned and "ghp_" not in cleaned and "sk-bbbb" not in cleaned and "AKIACCCC" not in cleaned
    assert home not in cleaned


def test_a_failing_sink_never_breaks_the_decision(monkeypatch, tmp_path):
    monkeypatch.setenv("PRAXEON_TRACE_SUPERVISOR", str(tmp_path))     # es un directorio: no se puede abrir como archivo
    assert _evaluate().provider == "laya"
