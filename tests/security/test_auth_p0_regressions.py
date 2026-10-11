"""Regresiones de seguridad P0: bypass de login, tipo de token, RBAC por ruta y secreto JWT."""

import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.server.dependencies import get_auth_service
from praxeon.server.services.auth_service import AuthService, Role, decode_jwt_hs256, encode_jwt_hs256

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


def _login(client, role="viewer"):
    r = client.post("/v1/auth/login", json={"api_key": API_KEY, "role": role})
    assert r.status_code == 200
    client.cookies.clear()
    return r.json()["data"]["tokens"]


def test_login_without_api_key_is_rejected(client):
    assert client.post("/v1/auth/login", json={"role": "admin"}).status_code == 401
    assert client.post("/v1/auth/login", json={"api_key": "mala", "role": "admin"}).status_code == 401


def test_refresh_token_cannot_be_used_as_access_token(client):
    tokens = _login(client, "admin")
    bearer = {"Authorization": "Bearer " + tokens["refresh_token"]}
    assert client.get("/v1/auth/me", headers=bearer).status_code == 401
    ok = {"Authorization": "Bearer " + tokens["access_token"]}
    assert client.get("/v1/auth/me", headers=ok).status_code == 200


def test_viewer_cannot_mutate_but_can_read(client):
    h = {"Authorization": "Bearer " + _login(client, "viewer")["access_token"]}
    assert client.get("/v1/workflows", headers=h).status_code == 200
    assert client.post("/v1/workflows", json={"name": "x"}, headers=h).status_code == 403


def test_non_ascii_api_key_does_not_cause_500(client):
    r = client.get("/v1/auth/me", headers={"X-API-Key": "clave-ñ".encode("latin-1")})
    assert r.status_code == 401


def test_jwt_requires_exp_and_issuer():
    with pytest.raises(ValueError):
        decode_jwt_hs256(encode_jwt_hs256({"iss": "praxeon-auth", "type": "access"}, SECRET), SECRET)
    with pytest.raises(ValueError):
        decode_jwt_hs256(encode_jwt_hs256({"iss": "otro", "exp": 9999999999, "type": "access"}, SECRET), SECRET)


def test_default_secret_is_not_predictable(monkeypatch):
    monkeypatch.delenv("PRAXEON_SECRET_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    a, b = AuthService(), AuthService()
    forged = encode_jwt_hs256(
        {"iss": "praxeon-auth", "type": "access", "exp": 9999999999, "role": Role.ADMIN.value, "jti": "x"},
        "praxeon_default_development_secret_key_32_chars!",
    )
    with pytest.raises(ValueError):
        a.authenticate_jwt(forged)
    assert a._secret_key != b._secret_key


def test_metrics_requires_auth_and_docs_hidden_in_production(client):
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"X-API-Key": API_KEY}).status_code == 200
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_is_observational_rejects_mutating_commands():
    from praxeon.policy.registry import ToolRegistry
    reg = ToolRegistry()
    for bad in ["ls; rm -rf /", "cat a > ~/.bashrc", "find . -delete", "git branch -D main", "lsof", "dir && del x"]:
        assert reg.is_observational("run_command", {"command": bad}) is False, bad
    for good in ["ls -la", "git status", "git log --oneline", "cat README.md", "pwd"]:
        assert reg.is_observational("run_command", {"command": good}) is True, good
