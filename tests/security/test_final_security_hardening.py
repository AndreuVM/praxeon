"""Endurecimiento final: proxy inverso, CSRF por cookie, flags de cookie, revocación persistente,
WebSocket sin clave en URL desde clientes remotos y FULL_ACCESS sin ejecución por nombre arbitrario."""

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from praxeon.runtime.full_access import is_executable_tool_name
from praxeon.server.app import create_app
from praxeon.server.dependencies import get_auth_service
from praxeon.server.services.auth_service import AuthService, Role

API_KEY = "a-very-long-secret-api-key-1234567890"
SECRET = "s" * 48


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PRAXEON_API_KEY", API_KEY)
    monkeypatch.setenv("PRAXEON_SECRET_KEY", SECRET)
    monkeypatch.setenv("PRAXEON_PROFILE", "production")
    monkeypatch.setenv("PRAXEON_CORS_ORIGINS", "https://example.com")
    app = create_app()
    svc = AuthService(api_key=API_KEY, secret_key=SECRET)
    app.dependency_overrides[get_auth_service] = lambda: svc
    return TestClient(app)


def _login(client, headers=None):
    r = client.post("/v1/auth/login", json={"api_key": API_KEY, "role": "admin"}, headers=headers or {})
    assert r.status_code == 200
    return r


# ---------------------------------------------------------------- proxy inverso
def test_proxy_headers_make_loopback_client_external(monkeypatch):
    for key in ("PRAXEON_API_KEY", "PRAXEON_SECRET_KEY", "PRAXEON_PROFILE", "PRAXEON_ENV", "PRAXEON_REQUIRE_AUTH"):
        monkeypatch.delenv(key, raising=False)
    c = TestClient(create_app(profile="dev"))
    assert c.get("/v1/sessions").status_code == 200  # cliente local directo: modo desarrollo
    assert c.get("/v1/sessions", headers={"X-Forwarded-For": "203.0.113.9"}).status_code != 200


# ---------------------------------------------------------------- cookies y CSRF
def test_cookies_are_strict_and_secure_over_https(client):
    r = _login(client, headers={"X-Forwarded-Proto": "https"})
    cookies = r.headers.get_list("set-cookie")
    assert cookies and all("HttpOnly" in c and "SameSite=strict" in c and "Secure" in c for c in cookies)


def test_cookie_authenticated_post_from_foreign_origin_is_blocked(client):
    _login(client)  # el cliente conserva las cookies HttpOnly
    foreign = client.post("/v1/workflows", json={"name": "x"}, headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    same = client.post("/v1/workflows", json={"name": "x"}, headers={"Origin": "http://testserver"})
    assert same.status_code == 200


def test_header_authenticated_request_is_not_subject_to_csrf_check(client):
    r = client.post("/v1/workflows", json={"name": "x"},
                    headers={"X-API-Key": API_KEY, "Origin": "https://evil.example"})
    assert r.status_code == 200


# ---------------------------------------------------------------- revocación persistente
def test_revoked_token_stays_revoked_after_restart(tmp_path):
    db = str(tmp_path / "rev.db")
    first = AuthService(secret_key=SECRET, revocation_db_path=db)
    pair = first.issue_token_pair(role=Role.ADMIN)
    assert first.revoke_token(pair.access_token) is True
    restarted = AuthService(secret_key=SECRET, revocation_db_path=db)
    with pytest.raises(ValueError):
        restarted.authenticate_jwt(pair.access_token)


def test_refresh_rotation_survives_restart(tmp_path):
    db = str(tmp_path / "rev.db")
    first = AuthService(secret_key=SECRET, revocation_db_path=db)
    pair = first.issue_token_pair(role=Role.OPERATOR)
    first.refresh_token_pair(pair.refresh_token)
    restarted = AuthService(secret_key=SECRET, revocation_db_path=db)
    with pytest.raises(ValueError):
        restarted.refresh_token_pair(pair.refresh_token)  # reutilización detectada tras reinicio


def test_expired_revocations_are_purged(tmp_path):
    db = str(tmp_path / "rev.db")
    svc = AuthService(secret_key=SECRET, revocation_db_path=db)
    pair = svc.issue_token_pair(role=Role.VIEWER, access_ttl_seconds=1)
    svc.revoke_token(pair.access_token)
    time.sleep(1.2)
    assert AuthService(secret_key=SECRET, revocation_db_path=db)._revoked_jtis == set()


# ---------------------------------------------------------------- WebSocket
def test_ws_ticket_requires_explicit_session(client):
    r = client.post("/v1/auth/ws-ticket", headers={"X-API-Key": API_KEY})
    assert r.status_code == 400


def test_ws_query_token_is_ignored_behind_a_proxy(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/v1/sessions/s1/stream?token={API_KEY}",
            headers={"X-Forwarded-For": "203.0.113.9", "Origin": "https://example.com"},
        ):
            pass


# ---------------------------------------------------------------- FULL_ACCESS
@pytest.mark.parametrize("name", ["rm -rf /", "x; calc", "../evil", "a && b", "", "deploy_service", "run_command"])
def test_full_access_rejects_arbitrary_tool_names(name):
    assert is_executable_tool_name(name) is False


@pytest.mark.parametrize("name", ["git", "echo", "python3"])
def test_full_access_accepts_known_executables(name):
    assert is_executable_tool_name(name) is True
