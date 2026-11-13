"""El nonce del recibo se consume después de todas las validaciones (auditoría S-08) y los fallos al registrar agentes dejan traza (F-05)."""

import logging

import pytest

from praxeon.domain.decision import DecisionStatus
from praxeon.domain.models import ActionCandidate, DecisionReceipt, Goal, ToolCall, compute_action_hash
from praxeon.runtime.executor import PolicyViolation, SecureExecutor
from praxeon.runtime.state import SessionState


def _setup(mode: str):
    executor = SecureExecutor(insecure_skip_signature=True)
    executor.register_handler("echo_tool", lambda args: "ok")
    state = SessionState(session_id="s1", goal=Goal(objective="t"), metadata={"execution_mode": mode})
    action = ActionCandidate(id="a1", description="eco", tool_call=ToolCall(tool_name="echo_tool", arguments={}))
    receipt = DecisionReceipt(decision_id="d1", session_id="s1", action_id="a1", action_hash=compute_action_hash(action),
                              state_hash=state.compute_hash(), decision_status=DecisionStatus.ALLOW, execution_mode=mode)
    return executor, state, action, receipt


def test_a_receipt_rejected_by_the_full_access_gate_is_not_burned():
    executor, state, action, receipt = _setup("full_access")
    with pytest.raises(PolicyViolation, match="FULL_ACCESS"):
        executor.execute(action, state=state, receipt=receipt)
    executor.allow_full_access = True
    assert executor.execute(action, state=state, receipt=receipt).success


def test_a_valid_receipt_still_runs_only_once():
    executor, state, action, receipt = _setup("local_restricted")
    assert executor.execute(action, state=state, receipt=receipt).success
    with pytest.raises(PolicyViolation, match="Replay"):
        executor.execute(action, state=state, receipt=receipt)


def test_the_executor_keeps_no_unbounded_receipt_set():
    assert not hasattr(SecureExecutor(insecure_skip_signature=True), "_consumed_receipts")


class _BrokenRegistry:
    store = None

    def list_all(self):
        return []

    def register(self, agent):
        raise RuntimeError("disco lleno")

    def __getattr__(self, name):
        return lambda *a, **k: []


def test_a_default_agent_that_fails_to_register_is_logged(tmp_path, caplog, monkeypatch):
    from praxeon.server.dependencies import RuntimeApplicationService

    monkeypatch.delenv("PRAXEON_PROFILE", raising=False)
    with caplog.at_level(logging.WARNING, logger="praxeon.server.dependencies"):
        RuntimeApplicationService(db_dir=str(tmp_path / "c"), agent_registry=_BrokenRegistry())
    assert "disco lleno" in caplog.text and "developer" in caplog.text
