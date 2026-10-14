"""API /v1/mcp: alta, revisión, aprobación por huella y ajustes de riesgo de servidores MCP."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service

FAKE = str(Path(__file__).with_name("mcp_fake_server.py"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXEON_CACHE_DIR", str(tmp_path / "cache"))
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    yield TestClient(create_app(profile="dev"))
    service.mcp.close_all()
    set_runtime_service(None)


BODY = {"command": sys.executable, "args": [FAKE], "env": {"TOKEN": "valor-secreto"}}


def data(resp):
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["data"]


def test_full_review_and_approval_flow(client):
    assert data(client.get("/v1/mcp/servers")) == []

    created = data(client.put("/v1/mcp/servers/fake", json=BODY))
    assert created["status"] == "disabled" and created["enabled"] is False and created["tools"] == []
    assert created["env_keys"] == ["TOKEN"] and "valor-secreto" not in client.get("/v1/mcp/servers").text   # nunca se devuelven valores

    enabled = data(client.post("/v1/mcp/servers/fake/enable"))
    assert enabled["status"] == "pending_approval" and len(enabled["tools"]) == 5 and not any(t["exposed"] for t in enabled["tools"])

    stale = client.post("/v1/mcp/servers/fake/approve", json={"tools_hash": "0" * 64})
    assert stale.status_code == 409                                                          # huella distinta a la revisada

    approved = data(client.post("/v1/mcp/servers/fake/approve", json={"tools_hash": enabled["tools_hash"]}))
    assert approved["status"] == "running" and approved["approved"] and all(t["exposed"] for t in approved["tools"])

    tuned = data(client.put("/v1/mcp/servers/fake/tools/boom", json={"disabled": True}))
    boom = next(t for t in tuned["tools"] if t["name"] == "boom")
    assert boom["exposed"] is False and boom["override"]["disabled"] is True

    data(client.post("/v1/mcp/servers/fake/disable"))
    assert data(client.get("/v1/mcp/servers"))[0]["status"] == "disabled"
    assert data(client.delete("/v1/mcp/servers/fake")) == {"deleted": "fake"}
    assert data(client.get("/v1/mcp/servers")) == []


def test_changing_the_command_resets_enablement_and_approval(client):
    data(client.put("/v1/mcp/servers/fake", json=BODY))
    info = data(client.post("/v1/mcp/servers/fake/enable"))
    data(client.post("/v1/mcp/servers/fake/approve", json={"tools_hash": info["tools_hash"]}))

    changed = data(client.put("/v1/mcp/servers/fake", json={**BODY, "args": [FAKE, "--otra-cosa"]}))
    assert changed["enabled"] is False and changed["approved"] is False and changed["status"] == "disabled"


def test_validation_and_missing_servers(client):
    assert client.put("/v1/mcp/servers/Nombre Malo", json=BODY).status_code in (404, 422)
    assert client.put("/v1/mcp/servers/con__doble", json=BODY).status_code == 422
    assert client.put("/v1/mcp/servers/ok", json={"command": ""}).status_code == 422
    for call in (
        client.post("/v1/mcp/servers/nada/enable"),
        client.post("/v1/mcp/servers/nada/disable"),
        client.post("/v1/mcp/servers/nada/restart"),
        client.post("/v1/mcp/servers/nada/approve", json={"tools_hash": "x" * 64}),
        client.put("/v1/mcp/servers/nada/tools/echo", json={}),
        client.delete("/v1/mcp/servers/nada"),
    ):
        assert call.status_code == 404
    assert client.put("/v1/mcp/servers/ok/tools/x", json={"risk": "ENORME"}).status_code in (404, 422)


def test_routes_require_admin_permission(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXEON_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PRAXEON_API_KEY", "clave-de-prueba")
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    try:
        anon = TestClient(create_app(profile="dev"))
        assert anon.get("/v1/mcp/servers").status_code in (401, 403)
        assert anon.put("/v1/mcp/servers/fake", json=BODY).status_code in (401, 403)   # sin credenciales no se puede lanzar un programa
    finally:
        service.mcp.close_all()
        set_runtime_service(None)


def test_editing_a_server_can_keep_the_stored_env_values(client):
    from praxeon.server.dependencies import get_runtime_service

    data(client.put("/v1/mcp/servers/fake", json={**BODY, "env": {"TOKEN": "valor-secreto", "OTRA": "x"}}))
    # la UI solo conoce las claves: reenvía null para conservar el valor, y cambia el resto
    data(client.put("/v1/mcp/servers/fake", json={**BODY, "timeout_s": 90, "env": {"TOKEN": None, "NUEVA": "y"}}))
    stored = get_runtime_service().mcp.store.get("fake")
    assert stored.env == {"TOKEN": "valor-secreto", "NUEVA": "y"} and stored.timeout_s == 90
    assert client.put("/v1/mcp/servers/fake", json={**BODY, "env": {"INEXISTENTE": None}}).status_code == 422
