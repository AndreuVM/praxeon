"""Tests de validación para la API REST del Catálogo de Agentes (/v1/agents) (F4-04 / Task 22)."""

import os
import pytest
from fastapi.testclient import TestClient

from praxeon.agents.definition import AgentDefinition, AgentStatus, ModelConfig
from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    set_active_security_profile,
    set_runtime_service,
)


@pytest.fixture
def isolated_client(tmp_path):
    """Crea una instancia aislada del cliente de pruebas con almacenes temporales."""
    db_dir = str(tmp_path)
    service = RuntimeApplicationService(db_dir=db_dir)
    set_runtime_service(service)
    set_active_security_profile("dev")

    app = create_app(profile="dev")
    client = TestClient(app)
    try:
        yield client, service
    finally:
        set_runtime_service(None)
        set_active_security_profile(None)


def test_list_agents_default_seeded_and_filters(isolated_client):
    client, _ = isolated_client

    response = client.get("/v1/agents")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    agents = payload["data"]
    assert len(agents) >= 5  # Plantillas canónicas sembradas
    agent_ids = [a["agent_id"] for a in agents]
    assert "ag_developer" in agent_ids

    # Filtrar por rol
    resp_role = client.get("/v1/agents?role=Developer")
    assert resp_role.status_code == 200
    role_agents = resp_role.json()["data"]
    assert all(a["role"].lower() == "developer" for a in role_agents)
    assert len(role_agents) >= 1

    # Filtrar por status
    resp_status = client.get("/v1/agents?status=ACTIVE")
    assert resp_status.status_code == 200
    assert len(resp_status.json()["data"]) >= 1


def test_crud_agent_lifecycle(isolated_client):
    client, service = isolated_client

    # 1. Crear nuevo agente
    create_payload = {
        "agent_id": "ag_custom_tester",
        "name": "Custom Test Agent",
        "role": "QA Engineer",
        "description": "Specialized in running regression suites",
        "system_prompt": "You are a QA automation agent.",
        "capabilities": ["testing", "reporting"],
        "skills": ["pytest_runner"],
        "allowed_tools": ["pytest", "coverage"],
        "metadata": {"tags": ["qa", "ci"]},
    }
    create_resp = client.post("/v1/agents", json=create_payload)
    assert create_resp.status_code == 201
    created_data = create_resp.json()["data"]
    assert created_data["agent_id"] == "ag_custom_tester"
    assert created_data["version"] == 1
    assert created_data["status"] == "ACTIVE"
    assert len(created_data["definition_hash"]) == 16

    # 2. Intento duplicado produce 409 Conflict
    conflict_resp = client.post("/v1/agents", json=create_payload)
    assert conflict_resp.status_code == 409

    # 3. Consultar detalle
    get_resp = client.get("/v1/agents/ag_custom_tester")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["name"] == "Custom Test Agent"

    # 4. Actualización parcial con PATCH (incrementa versión determinista)
    patch_payload = {
        "description": "Updated QA Agent description",
        "capabilities": ["testing", "reporting", "benchmarking"],
    }
    patch_resp = client.patch("/v1/agents/ag_custom_tester", json=patch_payload)
    assert patch_resp.status_code == 200
    patched_data = patch_resp.json()["data"]
    assert patched_data["version"] == 2
    assert patched_data["description"] == "Updated QA Agent description"
    assert "benchmarking" in patched_data["capabilities"]

    # 5. Filtrar por tag y capability
    resp_tag = client.get("/v1/agents?tag=benchmarking")
    assert resp_tag.status_code == 200
    assert any(a["agent_id"] == "ag_custom_tester" for a in resp_tag.json()["data"])

    # 6. Soft Delete
    del_resp = client.delete("/v1/agents/ag_custom_tester")
    assert del_resp.status_code == 200
    assert del_resp.json()["data"]["status"] == "TERMINATED"

    # Verificar que el agente ahora tiene status TERMINATED
    after_del_resp = client.get("/v1/agents/ag_custom_tester")
    assert after_del_resp.status_code == 200
    assert after_del_resp.json()["data"]["status"] == "TERMINATED"

    # 7. Hard Delete
    hard_del_resp = client.delete("/v1/agents/ag_custom_tester?hard_delete=true")
    assert hard_del_resp.status_code == 200
    assert hard_del_resp.json()["data"]["status"] == "REMOVED"

    # Ahora debe retornar 404
    not_found_resp = client.get("/v1/agents/ag_custom_tester")
    assert not_found_resp.status_code == 404


def test_agent_immutable_versions_api(isolated_client):
    client, service = isolated_client

    # Registrar agente
    create_payload = {
        "agent_id": "ag_versioned",
        "name": "Versioned Agent",
        "role": "Architect",
        "system_prompt": "Initial prompt v1",
    }
    res = client.post("/v1/agents", json=create_payload)
    assert res.status_code == 201
    v1_hash = res.json()["data"]["definition_hash"]

    # Crear nueva versión inmutable vía /versions
    version_payload = {
        "system_prompt": "Revised prompt v2 with new guidelines",
        "description": "Version 2 release",
    }
    v2_res = client.post("/v1/agents/ag_versioned/versions", json=version_payload)
    assert v2_res.status_code == 201
    v2_data = v2_res.json()["data"]
    assert v2_data["version"] == 2
    assert v2_data["system_prompt"] == "Revised prompt v2 with new guidelines"
    assert v2_data["definition_hash"] != v1_hash

    # Listar historial de versiones
    hist_res = client.get("/v1/agents/ag_versioned/versions")
    assert hist_res.status_code == 200
    history = hist_res.json()["data"]
    assert len(history) >= 2
    versions = [h["version"] for h in history]
    assert 1 in versions and 2 in versions

    # Consultar versión histórica específica
    h1_res = client.get("/v1/agents/ag_versioned/versions/1")
    assert h1_res.status_code == 200
    assert h1_res.json()["data"]["system_prompt"] == "Initial prompt v1"


def test_auth_protection_on_agents_endpoints(tmp_path):
    os.environ["PRAXEON_SECRET_KEY"] = "super_secure_production_secret_key_32_chars_long"
    os.environ["PRAXEON_API_KEY"] = "prax_prod_valid_test_key_16_chars"
    os.environ["PRAXEON_CORS_ORIGINS"] = "http://localhost:5173"

    db_dir = str(tmp_path)
    service = RuntimeApplicationService(db_dir=db_dir)
    set_runtime_service(service)
    set_active_security_profile("production")


    try:
        app = create_app(profile="production")
        client = TestClient(app)

        # Sin cabecera de autenticación debe fallar 401
        unauth_resp = client.get("/v1/agents", headers={"host": "192.168.1.50"})
        assert unauth_resp.status_code in (401, 403)

        # Con clave válida debe autorizar
        auth_resp = client.get(
            "/v1/agents",
            headers={"X-API-Key": "prax_prod_valid_test_key_16_chars", "host": "192.168.1.50"}
        )
        assert auth_resp.status_code == 200
    finally:
        os.environ.pop("PRAXEON_SECRET_KEY", None)
        os.environ.pop("PRAXEON_API_KEY", None)
        os.environ.pop("PRAXEON_CORS_ORIGINS", None)
        set_runtime_service(None)
        set_active_security_profile(None)
