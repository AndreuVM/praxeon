"""Ciclo de vida de tokens, revocaciones y tickets WebSocket de ``AuthService`` (AUD-23, Q-03).

Cada test fija una propiedad de seguridad: un ticket sirve una vez, un refresh token no se reutiliza, una revocación
sobrevive al reinicio y un fallo del disco no deja pasar un token revocado ni tumba el servicio.
"""

import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest

from praxeon.server.services.auth_service import (
    ROLE_PERMISSIONS,
    AuthService,
    Role,
    decode_jwt_hs256,
    encode_jwt_hs256,
)

SECRET = "s" * 48


@pytest.fixture
def svc():
    return AuthService(api_key="k" * 24, secret_key=SECRET)


# --- tickets WebSocket ---------------------------------------------------------------------------------------------------
def test_a_websocket_ticket_is_valid_exactly_once(svc):
    ticket = svc.create_ws_ticket("s1")
    assert ticket.startswith("wst_")
    assert svc.verify_and_consume_ws_ticket(ticket, expected_session_id="s1") is True
    assert svc.verify_and_consume_ws_ticket(ticket, expected_session_id="s1") is False       # repetición


@pytest.mark.parametrize("ticket", ["", None, "wst_inventado"])
def test_an_empty_or_unknown_ticket_is_rejected(svc, ticket):
    assert svc.verify_and_consume_ws_ticket(ticket) is False


def test_an_expired_ticket_is_rejected_and_forgotten(svc):
    ticket = svc.create_ws_ticket("s1")
    svc._tickets[ticket]["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    assert svc.verify_and_consume_ws_ticket(ticket) is False
    assert ticket not in svc._tickets


def test_a_ticket_is_bound_to_its_session(svc):
    ticket = svc.create_ws_ticket("s1")
    assert svc.verify_and_consume_ws_ticket(ticket, expected_session_id="otra") is False
    assert svc.verify_and_consume_ws_ticket(ticket, expected_session_id="s1") is True        # el intento ajeno no lo gasta


def test_ticket_ttl_comes_from_the_service_unless_given(svc):
    default = svc.create_ws_ticket("s1")
    custom = svc.create_ws_ticket("s1", ttl_seconds=5)
    ttl_default = (svc._tickets[default]["expires_at"] - svc._tickets[default]["created_at"]).total_seconds()
    ttl_custom = (svc._tickets[custom]["expires_at"] - svc._tickets[custom]["created_at"]).total_seconds()
    assert ttl_default == svc.ticket_ttl_seconds and ttl_custom == 5


def test_creating_a_ticket_purges_the_expired_and_consumed_ones(svc):
    old = svc.create_ws_ticket("s1")
    svc._tickets[old]["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    used = svc.create_ws_ticket("s1")
    svc._tickets[used]["consumed"] = True
    fresh = svc.create_ws_ticket("s1")
    assert set(svc._tickets) == {fresh}


# --- JWT ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("token", ["", "a.b", "a.b.c.d"])
def test_a_token_without_three_segments_is_malformed(token):
    with pytest.raises(ValueError, match="3 segmentos"):
        decode_jwt_hs256(token, SECRET)


def test_a_token_signed_with_another_secret_is_rejected(svc):
    forged = AuthService(secret_key="x" * 48).issue_token_pair().access_token
    with pytest.raises(ValueError):
        svc.authenticate_jwt(forged)


def test_an_access_token_cannot_be_used_to_refresh(svc):
    with pytest.raises(ValueError, match="refresh"):
        svc.refresh_token_pair(svc.issue_token_pair().access_token)


def test_a_refresh_token_rotates_and_cannot_be_reused(svc):
    pair = svc.issue_token_pair(user_id="ana", role=Role.OPERATOR)
    renewed = svc.refresh_token_pair(pair.refresh_token)
    assert renewed.user_id == "ana" and renewed.role == Role.OPERATOR and renewed.refresh_token != pair.refresh_token
    with pytest.raises(ValueError, match="reutilización"):
        svc.refresh_token_pair(pair.refresh_token)


def test_revoking_a_token_makes_it_unusable(svc):
    token = svc.issue_token_pair().access_token
    assert svc.authenticate_jwt(token)
    assert svc.revoke_token(token) is True
    with pytest.raises(ValueError, match="revocado"):
        svc.authenticate_jwt(token)


@pytest.mark.parametrize("token", ["no-es-un-token", ""])
def test_revoking_an_invalid_token_reports_failure_instead_of_raising(svc, token):
    assert svc.revoke_token(token) is False


def _access_token(**claims):
    now = int(time.time())
    payload = {"iss": "praxeon-auth", "sub": "u", "tenant_id": "t", "jti": "j1", "iat": now, "exp": now + 600,
               "type": "access", **claims}
    return encode_jwt_hs256(payload, SECRET)


def test_a_token_without_explicit_permissions_gets_those_of_its_role(svc):
    context = svc.authenticate_jwt(_access_token(role="operator", permissions=[]))
    assert context.permissions == ROLE_PERMISSIONS[Role.OPERATOR]


def test_an_unknown_role_in_a_token_falls_back_to_the_least_privileged(svc):
    context = svc.authenticate_jwt(_access_token(role="superusuario", permissions=[]))
    assert context.role == Role.VIEWER and context.permissions == ROLE_PERMISSIONS[Role.VIEWER]


def test_unknown_permission_names_in_a_token_are_ignored(svc):
    context = svc.authenticate_jwt(_access_token(role="viewer", permissions=["inventado:todo"]))
    assert context.permissions == ROLE_PERMISSIONS[Role.VIEWER]


# --- API key -----------------------------------------------------------------------------------------------------------
def test_api_key_verification(svc):
    assert svc.verify_api_key("k" * 24) and svc.verify_api_key("  " + "k" * 24 + "\n")
    assert not svc.verify_api_key("mala") and not svc.verify_api_key(None) and not svc.verify_api_key("")


def test_without_a_configured_key_nothing_verifies(monkeypatch):
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    assert AuthService(api_key=None, secret_key=SECRET).verify_api_key("cualquiera") is False


# --- revocaciones persistentes -------------------------------------------------------------------------------------------
def test_a_revocation_survives_a_restart(tmp_path):
    db = str(tmp_path / "revocations.db")
    first = AuthService(secret_key=SECRET, revocation_db_path=db)
    token = first.issue_token_pair().access_token
    assert first.revoke_token(token)
    restarted = AuthService(secret_key=SECRET, revocation_db_path=db)
    with pytest.raises(ValueError, match="revocado"):
        restarted.authenticate_jwt(token)


def test_expired_revocations_are_forgotten_when_a_new_one_is_recorded(svc):
    svc._remember_revocation("viejo", time.time() - 10)
    svc._remember_revocation("nuevo", time.time() + 600)
    assert "viejo" not in svc._revoked_jtis and "nuevo" in svc._revoked_jtis


def test_a_revocation_without_expiry_is_kept_for_a_week(svc):
    svc._remember_revocation("sin-exp", None)
    assert svc._revoked_exp["sin-exp"] > time.time() + 6 * 86400


def test_an_unreadable_revocation_store_does_not_stop_the_service(tmp_path, caplog):
    """Un directorio donde debería haber una base de datos: se registra el aviso y el servicio sigue funcionando."""
    with caplog.at_level(logging.ERROR):
        service = AuthService(secret_key=SECRET, revocation_db_path=str(tmp_path))
    assert "No se pudieron cargar las revocaciones" in caplog.text
    assert service.authenticate_jwt(service.issue_token_pair().access_token)


def test_a_failing_revocation_store_still_revokes_in_memory(tmp_path, caplog, monkeypatch):
    service = AuthService(secret_key=SECRET, revocation_db_path=str(tmp_path / "rev.db"))

    def broken(*_args, **_kwargs):
        raise sqlite3.OperationalError("disco lleno")

    monkeypatch.setattr(service._revocations, "remember", broken)
    token = service.issue_token_pair().access_token
    with caplog.at_level(logging.ERROR):
        assert service.revoke_token(token) is True
    assert "No se pudo guardar la revocación" in caplog.text
    with pytest.raises(ValueError, match="revocado"):
        service.authenticate_jwt(token)
