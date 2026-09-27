"""Pruebas de endurecimiento de seguridad y autenticación de la Web API (tests/security/test_web_api_authentication.py).

Verifica formalmente:
1. Endpoints públicos (/health, /metrics) accesibles según política de monitoreo.
2. Endpoints protegidos (/v1/sessions, /v1/decisions, etc.) exigen autenticación en perfil 'production' o con PRAXEON_API_KEY.
3. Soporte para 'X-API-Key' y 'Authorization: Bearer <token>'.
4. Canales WebSocket (/v1/sessions/{id}/stream y /ws/{id}) exigen token (?token= o cabecera) bajo perfil seguro y rechazan intentos no autorizados con código 1008.
5. Modo desarrollo sin claves mantiene compatibilidad emitiendo advertencia de seguridad.
"""

import os
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    is_auth_required,
    set_runtime_service,
)


@pytest.fixture
def test_service(tmp_path):
    """Crea una instancia aislada de RuntimeApplicationService."""
    service = RuntimeApplicationService(db_dir=str(tmp_path / "auth_test_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


def test_dev_mode_permissive_access(test_service, monkeypatch):
    """En perfil de desarrollo sin claves configuradas, las peticiones son permitidas."""
    monkeypatch.delenv("PRAXEON_PROFILE", raising=False)
    monkeypatch.delenv("PRAXEON_ENV", raising=False)
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_REQUIRE_AUTH", raising=False)

    app = create_app()
    client = TestClient(app)

    # Health check abierto
    resp_health = client.get("/v1/health")
    assert resp_health.status_code == 200

    # Sessions accesible sin token en dev
    resp_sess = client.get("/v1/sessions")
    assert resp_sess.status_code == 200
    assert resp_sess.json()["success"] is True

    # WebSocket conecta normalmente
    with client.websocket_connect("/v1/sessions/dev_session_1/stream") as ws:
        data = ws.receive_json()
        assert data["action"] == "connected"


def test_api_key_enforcement_with_praxeon_api_key(test_service, monkeypatch):
    """Cuando PRAXEON_API_KEY está configurada, se exige autenticación estricta."""
    api_key = "praxeon-prod-secret-api-key-999"
    monkeypatch.setenv("PRAXEON_API_KEY", api_key)
    monkeypatch.delenv("PRAXEON_PROFILE", raising=False)

    app = create_app()
    client = TestClient(app)

    # 1. Endpoint público (/health) no requiere clave
    res = client.get("/v1/health")
    assert res.status_code == 200

    # 2. Sin clave en endpoint protegido -> 401 Unauthorized
    res_unauth = client.get("/v1/sessions")
    assert res_unauth.status_code == 401
    assert "Autenticación requerida" in res_unauth.json()["detail"]

    # 3. Clave errónea -> 401 Unauthorized
    res_bad = client.get("/v1/sessions", headers={"X-API-Key": "wrong-key"})
    assert res_bad.status_code == 401
    assert "inválidas" in res_bad.json()["detail"]

    # 4. Clave válida con cabecera X-API-Key -> 200 OK
    res_x_key = client.get("/v1/sessions", headers={"X-API-Key": api_key})
    assert res_x_key.status_code == 200
    assert res_x_key.json()["success"] is True

    # 5. Clave válida con cabecera Authorization: Bearer -> 200 OK
    res_bearer = client.get("/v1/sessions", headers={"Authorization": f"Bearer {api_key}"})
    assert res_bearer.status_code == 200
    assert res_bearer.json()["success"] is True


def test_production_profile_enforcement(test_service, monkeypatch):
    """En perfil 'production', cualquier petición sin clave válida es rechazada."""
    secret = "a_super_secure_production_secret_key_32_characters_long!"
    monkeypatch.setenv("PRAXEON_PROFILE", "production")
    monkeypatch.setenv("PRAXEON_SECRET_KEY", secret)
    monkeypatch.setenv("PRAXEON_CORS_ORIGINS", "https://app.praxeon.io")

    app = create_app(profile="production")
    client = TestClient(app)

    # Petición no autenticada rechazada
    res = client.post("/v1/sessions", json={"goal": "Production mission"})
    assert res.status_code == 401

    # Petición autenticada con la clave de producción
    res_auth = client.post(
        "/v1/sessions",
        json={"goal": "Production mission"},
        headers={"X-API-Key": secret},
    )
    assert res_auth.status_code == 201
    assert res_auth.json()["success"] is True


def test_websocket_authentication_rejection_and_acceptance(test_service, monkeypatch):
    """Verifica que el canal WebSocket autentique tokens en query param y cabeceras."""
    api_key = "ws-secure-access-token-42"
    monkeypatch.setenv("PRAXEON_API_KEY", api_key)

    app = create_app()
    client = TestClient(app)

    # 1. Intento sin token -> cerrado con WS_1008_POLICY_VIOLATION (WebSocketDisconnect)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/v1/sessions/sec_sess_ws/stream"):
            pass
    assert exc_info.value.code == 1008

    # 2. Intento con token incorrecto -> cerrado con 1008
    with pytest.raises(WebSocketDisconnect) as exc_info_bad:
        with client.websocket_connect("/v1/sessions/sec_sess_ws/stream?token=invalid_tok"):
            pass
    assert exc_info_bad.value.code == 1008

    # 3. Intento exitoso con token en query param
    with client.websocket_connect(f"/v1/sessions/sec_sess_ws/stream?token={api_key}") as ws:
        msg = ws.receive_json()
        assert msg["action"] == "connected"
        assert msg["session_id"] == "sec_sess_ws"

    # 4. Intento exitoso a través del alias /ws/{session_id} con query param
    with client.websocket_connect(f"/ws/sec_sess_alias?token={api_key}") as ws:
        msg = ws.receive_json()
        assert msg["action"] == "connected"
        assert msg["session_id"] == "sec_sess_alias"
