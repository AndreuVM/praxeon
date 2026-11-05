"""Cabecera Host de confianza y token por arranque (auditoría S-05)."""

import pytest
from fastapi.testclient import TestClient

import os
from praxeon.server.app import create_app, ensure_local_token, validate_network_binding
from praxeon.server.host_guard import host_is_allowed, LOOPBACK_HOSTS


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("PRAXEON_ALLOWED_HOSTS", "PRAXEON_API_KEY", "PRAXEON_ALLOW_INSECURE_DEV", "PRAXEON_SECRET_KEY",
                 "PRAXEON_PROFILE", "PRAXEON_ENV", "PRAXEON_REQUIRE_AUTH"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("header,ok", [
    ("localhost", True), ("localhost:8000", True), ("127.0.0.1:51234", True), ("[::1]:8000", True), ("[::1]", True),
    ("evil.example", False), ("evil.example:8000", False), ("127.0.0.1.evil.example", False), ("localhost.evil.example", False), ("", False),
])
def test_default_hosts_are_loopback_only(header, ok):
    assert host_is_allowed(header, LOOPBACK_HOSTS) is ok


def test_wildcard_subdomains_but_not_the_bare_suffix():
    allowed = ["*.praxeon.test", "api.otro.test"]
    assert host_is_allowed("a.praxeon.test:443", allowed) and host_is_allowed("api.otro.test", allowed)
    assert not host_is_allowed("praxeon.test", allowed) and not host_is_allowed("evilpraxeon.test", allowed)


def test_a_dns_rebinding_request_is_rejected_before_reaching_the_api():
    client = TestClient(create_app(), base_url="http://evil.example:8000")
    res = client.get("/v1/health")
    assert res.status_code == 400 and "Invalid host" in res.text
    assert TestClient(create_app(), base_url="http://localhost:8000").get("/v1/health").status_code != 400


def test_the_operator_can_declare_the_names_the_server_answers_to(monkeypatch):
    monkeypatch.setenv("PRAXEON_ALLOWED_HOSTS", "praxeon.midominio.test")
    assert TestClient(create_app(), base_url="http://praxeon.midominio.test").get("/v1/health").status_code != 400
    assert TestClient(create_app(), base_url="http://localhost").get("/v1/health").status_code == 400


def test_a_websocket_with_a_foreign_host_is_closed():
    from starlette.websockets import WebSocketDisconnect
    client = TestClient(create_app(), base_url="http://evil.example")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("ws://evil.example/ws/s-1"):
            pass


def test_serve_generates_a_token_per_run_unless_insecure_dev_is_requested(monkeypatch):
    monkeypatch.setenv("PRAXEON_API_KEY", "")                            # registra la variable para que monkeypatch la restaure
    monkeypatch.delenv("PRAXEON_API_KEY")
    token = ensure_local_token("127.0.0.1")
    assert token and len(token) >= 32 and os.environ["PRAXEON_API_KEY"] == token
    assert ensure_local_token("127.0.0.1") is None                       # ya hay clave: no se cambia
    monkeypatch.delenv("PRAXEON_API_KEY")
    monkeypatch.setenv("PRAXEON_ALLOW_INSECURE_DEV", "1")
    assert ensure_local_token("127.0.0.1") is None and "PRAXEON_API_KEY" not in os.environ


def test_exposing_the_server_needs_an_api_key_and_declared_hosts(monkeypatch):
    with pytest.raises(ValueError, match="PRAXEON_API_KEY"):
        validate_network_binding("0.0.0.0")
    monkeypatch.setenv("PRAXEON_API_KEY", "k" * 20)
    with pytest.raises(ValueError, match="PRAXEON_ALLOWED_HOSTS"):
        validate_network_binding("0.0.0.0")
    monkeypatch.setenv("PRAXEON_ALLOWED_HOSTS", "praxeon.midominio.test")
    validate_network_binding("0.0.0.0")
    validate_network_binding("127.0.0.1")
