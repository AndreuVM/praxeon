"""Las rutas sin clave fallan cerradas (auditoría S-07)."""

import logging
import sqlite3

import pytest

from praxeon.domain.models import ActionCandidate, DecisionReceipt, DecisionStatus, Goal, ToolCall, compute_action_hash
from praxeon.runtime import PolicyViolation, SecureExecutor, SessionState
from praxeon.server.services.auth_service import AuthService


def _allowed_unsigned_receipt():
    state = SessionState(session_id="s1", goal=Goal(objective="t"))
    action = ActionCandidate(id="a1", description="listar", tool_call=ToolCall(tool_name="list_dir", arguments={"path": "."}))
    receipt = DecisionReceipt(decision_id="d1", session_id="s1", action_id="a1", action_hash=compute_action_hash(action),
                              state_hash=state.compute_hash(), decision_status=DecisionStatus.ALLOW, reason_codes=["T"])
    return action, state, receipt


def test_an_executor_without_a_key_refuses_every_receipt():
    action, state, receipt = _allowed_unsigned_receipt()
    with pytest.raises(PolicyViolation, match="no tiene clave"):
        SecureExecutor(dry_run=True).execute(action, state=state, receipt=receipt)


def test_skipping_the_signature_check_must_be_asked_for_explicitly():
    action, state, receipt = _allowed_unsigned_receipt()
    SecureExecutor(dry_run=True, insecure_skip_signature=True).execute(action, state=state, receipt=receipt)


def test_with_a_key_an_unsigned_receipt_is_still_refused():
    action, state, receipt = _allowed_unsigned_receipt()
    with pytest.raises(PolicyViolation, match="carece de firma"):
        SecureExecutor(dry_run=True, secret_key="k" * 40).execute(action, state=state, receipt=receipt)


def test_verify_api_key_is_false_when_no_key_is_configured(monkeypatch):
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    svc = AuthService(api_key=None, secret_key="s" * 40)
    assert svc.verify_api_key("cualquiera") is False and svc.verify_api_key(None) is False


def test_a_failure_saving_a_revocation_is_logged(tmp_path, caplog, monkeypatch):
    svc = AuthService(api_key="k" * 20, secret_key="s" * 40, revocation_db_path=str(tmp_path / "rev.db"))

    def boom():
        raise sqlite3.OperationalError("disco lleno")

    monkeypatch.setattr(svc, "_revocation_conn", boom)
    with caplog.at_level(logging.ERROR, logger="praxeon.server.services.auth_service"):
        with svc._lock:
            svc._remember_revocation("jti-1", None)
    assert "jti-1" in caplog.text and "disco lleno" in caplog.text
    assert "jti-1" in svc._revoked_jtis                      # sigue revocado en memoria
