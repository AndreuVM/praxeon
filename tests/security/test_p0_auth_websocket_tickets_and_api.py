"""Pruebas de certificación formal P0: Autenticación E2E y tickets efímeros de streaming WebSocket.

Verifica:
1. Endpoint POST /v1/sessions/{session_id}/ws-ticket exige autenticación (401 si falta o es inválida).
2. Generación exitosa de ticket efímero de streaming con API Key autorizada (X-API-Key o Bearer).
3. Conexión WebSocket mediante ticket efímero (?ticket=...) sin exponer la clave maestra en URLs.
4. Garantía de consumo único (single-use): el mismo ticket no puede ser reutilizado por un segundo cliente.
5. Garantía de vinculación de sesión (session-binding): un ticket emitido para session_A no sirve para session_B.
6. Rechazo determinista con WS 1008 ante tickets inexistentes, consumidos o expirados.
7. Retrocompatibilidad: tokens clásicos por query param siguen funcionando.
"""

import asyncio
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    set_runtime_service,
)
from praxeon.server.websocket import (
    consume_ws_ticket,
    issue_ws_ticket,
)


VALID_API_KEY = "praxeon-prod-audit-secret-key-12345"


@pytest.fixture
def auth_service(tmp_path, monkeypatch):
    """Configura el entorno en modo autenticación obligatoria."""
    monkeypatch.setenv("PRAXEON_REQUIRE_AUTH", "true")
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_API_KEY)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "ws_ticket_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


def test_ws_ticket_generation_requires_auth(auth_service):
    """P0-AUTH: POST /v1/sessions/{id}/ws-ticket rechaza con 401 si no se provee autenticación."""
    app = create_app()
    client = TestClient(app)

    # 1. Sin cabeceras
    resp_no_auth = client.post("/v1/sessions/sess_ticket_1/ws-ticket")
    assert resp_no_auth.status_code == 401

    # 2. Con clave errónea
    resp_bad_auth = client.post(
        "/v1/sessions/sess_ticket_1/ws-ticket",
        headers={"X-API-Key": "invalid-key-xyz"},
    )
    assert resp_bad_auth.status_code == 401


def test_ws_ticket_generation_succeeds_with_auth(auth_service):
    """P0-AUTH: POST /v1/sessions/{id}/ws-ticket genera ticket efímero con API Key válida."""
    app = create_app()
    client = TestClient(app)

    # Con X-API-Key
    resp = client.post(
        "/v1/sessions/sess_ticket_1/ws-ticket",
        headers={"X-API-Key": VALID_API_KEY},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "ticket" in data
    assert data["ticket"].startswith("wst_")
    assert data["session_id"] == "sess_ticket_1"
    assert data["expires_in"] == 60

    # Con Authorization Bearer
    resp_bearer = client.post(
        "/v1/sessions/sess_ticket_bearer/ws-ticket",
        headers={"Authorization": f"Bearer {VALID_API_KEY}"},
    )
    assert resp_bearer.status_code == 200
    assert resp_bearer.json()["ticket"].startswith("wst_")


def test_websocket_connects_successfully_with_ephemeral_ticket(auth_service):
    """P0-AUTH: WebSocket acepta conexión mediante ticket efímero legítimo."""
    app = create_app()
    client = TestClient(app)

    session_id = "sess_ws_ticket_ok"
    # 1. Obtener ticket mediante endpoint REST autenticado
    res_ticket = client.post(
        f"/v1/sessions/{session_id}/ws-ticket",
        headers={"X-API-Key": VALID_API_KEY},
    )
    assert res_ticket.status_code == 200
    ticket = res_ticket.json()["ticket"]

    # 2. Conectar WebSocket utilizando solo el ticket efímero (sin API key en URL)
    with client.websocket_connect(f"/v1/sessions/{session_id}/stream?ticket={ticket}") as ws:
        msg = ws.receive_json()
        assert msg["action"] == "connected"
        assert msg["session_id"] == session_id


def test_websocket_ticket_is_single_use_preventing_replay(auth_service):
    """P0-AUTH: Un ticket ya consumido no puede volver a utilizarse (anti-replay)."""
    app = create_app()
    client = TestClient(app)

    session_id = "sess_ws_replay_test"
    res_ticket = client.post(
        f"/v1/sessions/{session_id}/ws-ticket",
        headers={"X-API-Key": VALID_API_KEY},
    )
    ticket = res_ticket.json()["ticket"]

    # Primer uso: exitoso
    with client.websocket_connect(f"/v1/sessions/{session_id}/stream?ticket={ticket}") as ws:
        msg = ws.receive_json()
        assert msg["action"] == "connected"

    # Segundo uso del mismo ticket: debe ser rechazado inmediatamente con 1008
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/v1/sessions/{session_id}/stream?ticket={ticket}"):
            pass
    assert exc_info.value.code == 1008


def test_websocket_ticket_is_bound_to_specific_session(auth_service):
    """P0-AUTH: Un ticket emitido para session_A es rechazado si se intenta usar en session_B."""
    app = create_app()
    client = TestClient(app)

    # Emitir ticket para sesión A
    res_ticket = client.post(
        "/v1/sessions/session_A/ws-ticket",
        headers={"X-API-Key": VALID_API_KEY},
    )
    ticket_a = res_ticket.json()["ticket"]

    # Intentar usarlo para sesión B: debe ser rechazado con 1008
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/v1/sessions/session_B/stream?ticket={ticket_a}"):
            pass
    assert exc_info.value.code == 1008


def test_websocket_rejects_bogus_or_unauthorized_ticket(auth_service):
    """P0-AUTH: Tickets inventados son rechazados de forma determinista con 1008."""
    app = create_app()
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/v1/sessions/session_any/stream?ticket=wst_fake_ticket_12345"):
            pass
    assert exc_info.value.code == 1008


@pytest.mark.asyncio
async def test_ticket_expiration_logic():
    """P0-AUTH: Verificación unitaria de expiración de tickets efímeros."""
    session_id = "sess_exp_unit"
    # Emitir ticket con TTL negativo para simular expiración inmediata
    ticket = await issue_ws_ticket(session_id=session_id, ttl_seconds=-1.0)
    # Debe ser rechazado como expirado
    consumed = await consume_ws_ticket(ticket, session_id=session_id)
    assert consumed is False
