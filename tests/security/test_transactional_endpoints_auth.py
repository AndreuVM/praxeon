"""Pruebas E2E de autenticación de endpoints transaccionales sensibles (P0.1).

Verifica de forma exhaustiva y sistemática que todos los endpoints de mutación y control
rechazan con HTTP 401 Unauthorized ante tokens ausentes o inválidos:
- POST /v1/sessions (Creación de sesión)
- POST /v1/sessions/run (Ejecución de misión)
- POST /v1/sessions/{id}/actions (Propuesta de acción)
- POST /v1/sessions/{id}/pause (Pausa de misión)
- POST /v1/sessions/{id}/resume (Reanudación de misión)
- POST /v1/sessions/{id}/stop (Detención de misión)
- POST /v1/sessions/{id}/rollback (Reversión de sesión)
- POST /v1/decisions/{id}/confirm (Confirmación humana)
- POST /v1/decisions/{id}/reject (Rechazo humano)
- POST /v1/decisions/{id}/execute (Ejecución física de capability)
"""

import os
import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    set_runtime_service,
)


VALID_API_KEY = "praxeon-prod-audit-secret-key-12345"


@pytest.fixture
def auth_runtime(tmp_path, monkeypatch):
    """Inicializa servicio y activa autenticación obligatoria en entorno."""
    monkeypatch.setenv("PRAXEON_REQUIRE_AUTH", "true")
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_API_KEY)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "trans_auth_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


TRANSACTIONAL_POST_ENDPOINTS = [
    ("/v1/sessions", {"goal": "Test session"}),
    ("/v1/sessions/run", {"goal": "Test mission"}),
    ("/v1/sessions/sess_dummy/actions", {"tool": "read_file", "arguments": {"path": "test.txt"}}),
    ("/v1/sessions/sess_dummy/pause", {}),
    ("/v1/sessions/sess_dummy/resume", {}),
    ("/v1/sessions/sess_dummy/stop", {}),
    ("/v1/sessions/sess_dummy/rollback", {"checkpoint_id": "chk_0"}),
    ("/v1/decisions/dec_dummy/confirm", {"approved": True, "reason": "Operator test"}),
    ("/v1/decisions/dec_dummy/reject", {"reason": "Operator reject"}),
    ("/v1/decisions/dec_dummy/execute", {"execution_mode": "local_restricted"}),
]


@pytest.mark.parametrize("endpoint,payload", TRANSACTIONAL_POST_ENDPOINTS)
def test_transactional_endpoints_reject_unauthenticated_request(auth_runtime, endpoint, payload):
    """P0.1: Cada endpoint transaccional emite HTTP 401 si no se envía cabecera de autenticación."""
    app = create_app()
    client = TestClient(app)

    response = client.post(endpoint, json=payload)
    assert response.status_code == 401, (
        f"Fallo de seguridad: Endpoint {endpoint} no emitió 401 ante petición sin token. "
        f"Obtenido: {response.status_code} ({response.text})"
    )
    assert "Autenticación requerida" in response.text or "WWW-Authenticate" in response.headers


@pytest.mark.parametrize("endpoint,payload", TRANSACTIONAL_POST_ENDPOINTS)
def test_transactional_endpoints_reject_invalid_token(auth_runtime, endpoint, payload):
    """P0.1: Cada endpoint transaccional emite HTTP 401 ante token inválido tanto en X-API-Key como Bearer."""
    app = create_app()
    client = TestClient(app)

    # 1. Con cabecera X-API-Key errónea
    res_x = client.post(endpoint, json=payload, headers={"X-API-Key": "completely-invalid-key-xyz"})
    assert res_x.status_code == 401, f"Endpoint {endpoint} permitió paso con X-API-Key falsa"

    # 2. Con cabecera Authorization: Bearer errónea
    res_bearer = client.post(
        endpoint, json=payload, headers={"Authorization": "Bearer completely-invalid-bearer"}
    )
    assert res_bearer.status_code == 401, f"Endpoint {endpoint} permitió paso con Bearer falso"


@pytest.mark.parametrize("endpoint,payload", TRANSACTIONAL_POST_ENDPOINTS)
def test_transactional_endpoints_pass_auth_guard_with_valid_token(auth_runtime, endpoint, payload):
    """P0.1: Con token válido, la petición supera el guardia 401 (puede dar 200, 201, 404 por ID no existente, pero NUNCA 401)."""
    app = create_app()
    client = TestClient(app)

    response = client.post(endpoint, json=payload, headers={"X-API-Key": VALID_API_KEY})
    assert response.status_code != 401, (
        f"Endpoint {endpoint} rechazó inesperadamente con 401 un token válido. "
        f"Respuesta: {response.status_code} - {response.text}"
    )
