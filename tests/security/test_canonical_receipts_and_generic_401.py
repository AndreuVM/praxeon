"""Firma de recibos sobre JSON canónico (auditoría S-11) y mensajes 401 que no revelan el motivo (S-13)."""

import logging

from fastapi.testclient import TestClient

from praxeon.domain.decision import (
    DecisionReceipt,
    DecisionStatus,
    _legacy_receipt_signature,
    compute_receipt_signature,
    sign_receipt,
    verify_receipt_signature,
)

KEY = "k" * 48


def _receipt(**overrides):
    fields = dict(decision_id="d1", session_id="s1", action_id="a1", action_hash="h" * 64, state_hash="0" * 64,
                  decision_status=DecisionStatus.ALLOW)
    fields.update(overrides)
    return DecisionReceipt(**fields)


def test_fields_containing_the_old_separator_no_longer_collide():
    base = dict(state_hash="0" * 64, nonce="n", decision_status=DecisionStatus.ALLOW)
    a = dict(decision_id="d", session_id="s:x", action_hash="y", **base)
    b = dict(decision_id="d", session_id="s", action_hash="x:y", **base)
    # Con el formato antiguo, ambas combinaciones producían el mismo texto firmado
    assert _legacy_receipt_signature(KEY, **a) == _legacy_receipt_signature(KEY, **b)
    assert compute_receipt_signature(KEY, **a) != compute_receipt_signature(KEY, **b)


def test_signed_receipts_verify_and_tampering_is_detected():
    signed = sign_receipt(_receipt(), KEY)
    assert verify_receipt_signature(KEY, signed)
    assert not verify_receipt_signature(KEY, signed.model_copy(update={"session_id": "otra"}))
    assert not verify_receipt_signature("otra-clave" * 5, signed)


def test_receipts_signed_with_the_previous_format_are_accepted_for_now_with_a_warning(caplog):
    r = _receipt()
    legacy = _legacy_receipt_signature(
        KEY, decision_id=r.decision_id, session_id=r.session_id, action_hash=r.action_hash, state_hash=r.state_hash,
        nonce=r.nonce, decision_status=r.decision_status, expires_at=r.expires_at, execution_mode=r.execution_mode,
    )
    with caplog.at_level(logging.WARNING, logger="praxeon.domain.decision"):
        assert verify_receipt_signature(KEY, r.model_copy(update={"signature": legacy}))
    assert "formato anterior" in caplog.text


def test_a_rejected_jwt_does_not_tell_the_client_why(monkeypatch, tmp_path, caplog):
    from praxeon.server.app import create_app
    from praxeon.server.auth_deps import get_auth_service
    from praxeon.server.services.auth_service import AuthService

    monkeypatch.setenv("PRAXEON_API_KEY", "a" * 32)
    svc = AuthService(api_key="a" * 32, secret_key="s" * 48)
    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: svc
    client = TestClient(app, base_url="http://localhost:8000")

    expired = svc.issue_token_pair(access_ttl_seconds=-10).access_token
    revoked = svc.issue_token_pair().access_token
    svc.revoke_token(revoked)
    forged = revoked[:-4] + ("AAAA" if not revoked.endswith("AAAA") else "BBBB")

    with caplog.at_level(logging.INFO, logger="praxeon.server.dependencies"):
        details = {client.get("/v1/auth/me", headers={"Authorization": f"Bearer {t}"}).json()["detail"]
                   for t in (expired, revoked, forged)}
    assert details == {"Credenciales inválidas o caducadas."}
    assert "JWT rechazado" in caplog.text            # el motivo concreto queda en el log del servidor
