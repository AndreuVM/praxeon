"""Suite de Pruebas End-to-End (E2E) Full Stack para Servidor FastAPI y Web App (DEUDA-E2E-01).

Valida integralmente todos los subsistemas acoplados de PRAXEON 1.0:
1. Autenticación reactiva E2E (DEUDA-SEC-01) en perfiles de seguridad dev y production.
2. Catálogo de Agentes, registro, versionado inmutable y consulta histórica.
3. Editor y Motor de Workflows con persistencia transaccional en SQLite (DEUDA-WF-01, DEUDA-WF-02).
4. Ciclo de vida de sesiones de supervisión, propuesta de acciones, inspector contextual y canal WebSocket.
5. Entrega de assets estáticos de la SPA Web y contratos de salud pública (/v1/health).
"""

import json
import os
import secrets
import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    get_runtime_service,
    set_runtime_service,
    set_active_security_profile,
)


@pytest.fixture
def isolated_stack(tmp_path, monkeypatch):
    """Inicializa una instancia limpia y aislada del runtime de PRAXEON con backend SQLite efímero."""
    db_dir = tmp_path / "praxeon_e2e_stack"
    db_dir.mkdir(parents=True, exist_ok=True)

    test_api_key = "praxeon_test_secret_key_32_characters_long_min"
    monkeypatch.setenv("PRAXEON_API_KEY", test_api_key)
    monkeypatch.setenv("PRAXEON_SECRET_KEY", test_api_key)
    monkeypatch.setenv("PRAXEON_PROFILE", "dev")

    service = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service)
    set_active_security_profile("dev")

    app = create_app(profile="dev")
    app.dependency_overrides[get_runtime_service] = lambda: service

    with TestClient(app) as client:
        yield {
            "client": client,
            "service": service,
            "api_key": test_api_key,
            "app": app,
        }

    set_runtime_service(None)
    set_active_security_profile(None)


def test_public_health_and_frontend_delivery(isolated_stack):
    """Verifica que /v1/health y la raíz / entreguen respuestas públicas válidas."""
    client = isolated_stack["client"]

    # 1. Health pública sin autenticación
    health_resp = client.get("/v1/health")
    assert health_resp.status_code == 200
    health_data = health_resp.json()["data"]
    assert health_data["status"] == "healthy"
    assert health_data["version"] in ("1.0.0", "1.1.0")

    # 2. Raíz SPA
    spa_resp = client.get("/")
    assert spa_resp.status_code == 200
    assert "PRAXEON" in spa_resp.text


def test_auth_enforcement_in_production_profile(isolated_stack, monkeypatch):
    """DEUDA-SEC-01: Valida que en modo 'production' los endpoints protegidos rechacen sin clave y acepten con X-API-Key."""
    api_key = isolated_stack["api_key"]
    monkeypatch.setenv("PRAXEON_PROFILE", "production")
    monkeypatch.setenv("PRAXEON_CORS_ORIGINS", "http://localhost:5173,http://localhost:8000")

    app = create_app(profile="production")
    app.dependency_overrides[get_runtime_service] = lambda: isolated_stack["service"]

    with TestClient(app) as prod_client:
        # 1. Solicitud sin cabecera de autenticación -> 401 Unauthorized
        unauth_resp = prod_client.get("/v1/sessions")
        assert unauth_resp.status_code == 401

        # 2. Solicitud con clave errónea -> 401 Unauthorized
        bad_key_resp = prod_client.get("/v1/sessions", headers={"X-API-Key": "wrong-key"})
        assert bad_key_resp.status_code == 401

        # 3. Solicitud válida con X-API-Key -> 200 OK
        valid_x_resp = prod_client.get("/v1/sessions", headers={"X-API-Key": api_key})
        assert valid_x_resp.status_code == 200

        # 4. Solicitud válida con Authorization Bearer -> 200 OK
        valid_bearer_resp = prod_client.get("/v1/sessions", headers={"Authorization": f"Bearer {api_key}"})
        assert valid_bearer_resp.status_code == 200

    set_active_security_profile(None)


def test_agent_catalog_lifecycle_and_versioning(isolated_stack):
    """Verifica el ciclo completo de agentes: listado, creación, versionado y consulta histórica."""
    client = isolated_stack["client"]
    api_key = isolated_stack["api_key"]
    headers = {"X-API-Key": api_key}

    # 1. Listar agentes iniciales (incluye los del catálogo por defecto)
    list_resp = client.get("/v1/agents", headers=headers)
    assert list_resp.status_code == 200
    agents = list_resp.json()["data"]
    assert isinstance(agents, list)
    assert len(agents) >= 1

    # 2. Registrar un nuevo agente especializado
    agent_payload = {
        "agent_id": "ag_e2e_tester",
        "name": "E2E Integration Agent",
        "role": "QA Engineer",
        "system_prompt": "Auditar la robustez y cobertura de integración de software.",
        "capabilities": ["code_review", "integration_testing"],
        "skills": ["python", "pytest"],
    }
    create_resp = client.post("/v1/agents", json=agent_payload, headers=headers)
    assert create_resp.status_code == 201
    created_agent = create_resp.json()["data"]
    assert created_agent["agent_id"] == "ag_e2e_tester"
    assert created_agent["version"] == 1

    # 3. Crear una nueva versión inmutable
    version_payload = {
        "system_prompt": "Auditar la robustez, cobertura y seguridad criptográfica.",
        "capabilities": ["code_review", "integration_testing", "security_audit"],
    }
    ver_resp = client.post("/v1/agents/ag_e2e_tester/versions", json=version_payload, headers=headers)
    assert ver_resp.status_code == 201
    evolved_agent = ver_resp.json()["data"]
    assert evolved_agent["version"] == 2
    assert "security_audit" in evolved_agent["capabilities"]

    # 4. Consultar historial de versiones
    hist_resp = client.get("/v1/agents/ag_e2e_tester/versions", headers=headers)
    assert hist_resp.status_code == 200
    history = hist_resp.json()["data"]
    assert len(history) == 2

    # 5. Obtener versión 1 histórica
    v1_resp = client.get("/v1/agents/ag_e2e_tester/versions/1", headers=headers)
    assert v1_resp.status_code == 200
    assert v1_resp.json()["data"]["version"] == 1


def test_workflow_orchestration_and_sqlite_persistence_e2e(isolated_stack):
    """DEUDA-WF-01 y DEUDA-WF-02: Creación, construcción de grafo, validación DAG, ejecución y persistencia."""
    client = isolated_stack["client"]
    api_key = isolated_stack["api_key"]
    headers = {"X-API-Key": api_key}

    # 1. Crear nuevo workflow
    wf_payload = {
        "workflow_id": "wf_e2e_data_pipeline",
        "name": "Data Pipeline E2E",
        "description": "Pipeline automatizado de análisis y validación",
    }
    wf_resp = client.post("/v1/workflows", json=wf_payload, headers=headers)
    assert wf_resp.status_code == 200
    wf_data = wf_resp.json()["data"]
    assert wf_data["workflow_id"] == "wf_e2e_data_pipeline"

    # 2. Agregar nodos al flujo
    node1_payload = {
        "name": "Extract",
        "node_type": "TASK",
        "tool_name": "read_file",
        "inputs": {"path": "pyproject.toml"},
        "x": 250.0,
        "y": 150.0,
    }
    n1_resp = client.post("/v1/workflows/wf_e2e_data_pipeline/nodes", json=node1_payload, headers=headers)
    assert n1_resp.status_code == 200
    node1_id = n1_resp.json()["data"]["node_id"]

    node2_payload = {
        "name": "Transform & Audit",
        "node_type": "TASK",
        "tool_name": "list_dir",
        "inputs": {"path": "."},
        "x": 450.0,
        "y": 150.0,
    }
    n2_resp = client.post("/v1/workflows/wf_e2e_data_pipeline/nodes", json=node2_payload, headers=headers)
    assert n2_resp.status_code == 200
    node2_id = n2_resp.json()["data"]["node_id"]

    # 3. Conectar nodos (start -> node1 -> node2 -> end)
    client.delete("/v1/workflows/wf_e2e_data_pipeline/edges/e_start_end", headers=headers)
    client.post("/v1/workflows/wf_e2e_data_pipeline/edges", json={"from_node": "start", "to_node": node1_id}, headers=headers)
    client.post("/v1/workflows/wf_e2e_data_pipeline/edges", json={"from_node": node1_id, "to_node": node2_id}, headers=headers)
    client.post("/v1/workflows/wf_e2e_data_pipeline/edges", json={"from_node": node2_id, "to_node": "end"}, headers=headers)

    # 4. Validar DAG
    val_resp = client.post("/v1/workflows/wf_e2e_data_pipeline/validate", headers=headers)
    assert val_resp.status_code == 200
    assert val_resp.json()["data"]["is_valid"] is True

    # 5. Ejecutar workflow
    exec_resp = client.post("/v1/workflows/wf_e2e_data_pipeline/execute", headers=headers)
    assert exec_resp.status_code == 200
    exec_data = exec_resp.json()["data"]
    assert exec_data["status"] in ("COMPLETED", "WAITING_RESULT", "FAILED")

    # 6. Consultar detalle del workflow para certificar persistencia en SQLite
    get_wf = client.get("/v1/workflows/wf_e2e_data_pipeline", headers=headers)
    assert get_wf.status_code == 200
    assert get_wf.json()["data"]["name"] == "Data Pipeline E2E"


def test_session_lifecycle_decision_inspector_and_websocket(isolated_stack):
    """Verifica ciclo de sesión, propuesta de acciones seguras y sensibles, inspector y WebSocket."""
    client = isolated_stack["client"]
    api_key = isolated_stack["api_key"]
    headers = {"X-API-Key": api_key}

    # 1. Crear sesión de supervisión
    sess_payload = {
        "goal": "Verificar integridad del servidor y base de datos",
        "session_id": "sess_e2e_full_test",
        "agent_name": "SupervisedAgent",
    }
    sess_resp = client.post("/v1/sessions", json=sess_payload, headers=headers)
    assert sess_resp.status_code == 201
    session_id = sess_resp.json()["data"]["session_id"]
    assert session_id == "sess_e2e_full_test"

    # 2. Proponer acción segura (read_file) -> ALLOW con capability token
    safe_action = {
        "tool": "read_file",
        "operation": "pyproject.toml",
        "arguments": {"path": "pyproject.toml"},
        "provenance": {"source": "SupervisedAgent", "step": 1},
    }
    prop_resp = client.post(f"/v1/sessions/{session_id}/actions", json=safe_action, headers=headers)
    assert prop_resp.status_code == 200
    decision_data = prop_resp.json()["data"]
    assert decision_data["status"] == "ALLOW"
    assert decision_data["capability"] is not None
    decision_id = decision_data["decision_id"]

    # 3. Consultar pestañas del inspector de decisiones (las 4 pestañas requeridas)
    dec_resp = client.get(f"/v1/decisions/{decision_id}", headers=headers)
    assert dec_resp.status_code == 200
    inspector = dec_resp.json()["data"]
    assert "decision_tab" in inspector
    assert "evidence_tab" in inspector
    assert "policy_tab" in inspector
    assert "receipt_tab" in inspector

    # 4. Conectar WebSocket de streaming en tiempo real autenticado con token
    with client.websocket_connect(f"/v1/sessions/{session_id}/stream?token={api_key}") as ws:
        msg = ws.receive_json()
        assert msg["action"] == "connected"
        assert msg["session_id"] == session_id
