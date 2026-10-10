"""Tests de integración y contrato de API para el Web Server de PRAXEON 1.0 (Fase 2).

Verifica todos los endpoints REST y canales WebSocket especificados en la Sección 3:
- Creación, listado y snapshots de sesiones con Decision Tree.
- Propuesta de acciones respetando el pipeline formal (Proposal -> Evidence -> Risk -> Policy -> Capability).
- Inspector contextual de 4 pestañas: Decision, Evidence, Policy, Receipt.
- Confirmación humana de decisiones en REVIEW.
- Ejecución física en sandbox con capability token y prevención de ejecuciones no autorizadas (fail-closed).
- Stream paginado de eventos y canal WebSocket con sincronización en tiempo real.
- Endpoints de salud (/health) y telemetría agregada (/metrics).
"""

from datetime import datetime, timezone
import json
import pytest
from fastapi.testclient import TestClient

from praxeon.domain.events import EventType
from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    get_runtime_service,
    set_runtime_service,
)


@pytest.fixture
def test_service(tmp_path):
    """Crea una instancia aislada de RuntimeApplicationService con SQLite temporal."""
    service = RuntimeApplicationService(db_dir=str(tmp_path / "test_server_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


@pytest.fixture
def client(test_service):
    """Cliente de pruebas para FastAPI y WebSockets."""
    app = create_app()
    return TestClient(app)


def test_healthcheck_endpoint(client):
    """GET /v1/health responde 200 OK con versión y estado operativo."""
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "healthy"
    assert data["version"] in ("1.0.0", "1.1.0")
    assert "uptime_seconds" in data


def test_metrics_endpoint(client):
    """GET /v1/metrics responde con telemetría agregada."""
    resp = client.get("/v1/metrics")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "total_sessions" in data
    assert "decisions_by_status" in data
    assert "latency_summary" in data


def test_session_lifecycle(client):
    """Creación, consulta y listado de sesiones de supervisión."""
    # 1. Crear sesión
    create_payload = {
        "goal": "Depurar error de autenticación en la API",
        "agent_name": "CodingAgent",
    }
    resp = client.post("/v1/sessions", json=create_payload)
    assert resp.status_code == 201
    sess_data = resp.json()["data"]
    session_id = sess_data["session_id"]
    assert sess_data["goal"] == create_payload["goal"]
    assert sess_data["status"] == "Active"

    # 2. Listar sesiones
    list_resp = client.get("/v1/sessions")
    assert list_resp.status_code == 200
    sessions = list_resp.json()["data"]
    assert any(s["session_id"] == session_id for s in sessions)

    # 3. Snapshot de sesión con DecisionTree
    snap_resp = client.get(f"/v1/sessions/{session_id}")
    assert snap_resp.status_code == 200
    snapshot = snap_resp.json()["data"]
    assert snapshot["session_id"] == session_id
    assert "tree" in snapshot
    assert snapshot["tree"]["node_count"] >= 1  # Al menos nodo raíz 'Start'


def test_propose_action_safe_allow(client):
    """Propuesta de acción de bajo riesgo: se evalúa y autoriza con emisión de capability."""
    # Crear sesión previa
    s_resp = client.post("/v1/sessions", json={"goal": "Auditar dependencias"})
    session_id = s_resp.json()["data"]["session_id"]

    # Proponer lectura segura
    proposal = {
        "tool": "read_file",
        "operation": "pyproject.toml",
        "arguments": {"path": "pyproject.toml"},
        "provenance": {"source": "CodingAgent", "step": 1},
        "context": {"goal": "Auditar dependencias"},
    }
    prop_resp = client.post(f"/v1/sessions/{session_id}/actions", json=proposal)
    assert prop_resp.status_code == 200
    dec = prop_resp.json()["data"]
    assert dec["status"] == "ALLOW"
    assert dec["capability"] is not None
    assert dec["capability"]["allowed_tools"] == ["read_file"]
    assert "signature" in dec["capability"]

    # Verificar que el Decision Tree de la sesión creció
    snap = client.get(f"/v1/sessions/{session_id}").json()["data"]
    assert snap["tree"]["node_count"] >= 2


def test_propose_action_high_risk_requires_confirmation(client):
    """Acción de alto riesgo (destructiva o git push): genera estado REVIEW y espera confirmación."""
    s_resp = client.post("/v1/sessions", json={"goal": "Desplegar cambios"})
    session_id = s_resp.json()["data"]["session_id"]

    proposal = {
        "tool": "run_command",
        "operation": "git push origin main",
        "arguments": {"command": "git push origin main"},
        "provenance": {"source": "CodingAgent", "step": 2},
    }
    prop_resp = client.post(f"/v1/sessions/{session_id}/actions", json=proposal)
    assert prop_resp.status_code == 200
    dec = prop_resp.json()["data"]
    assert dec["status"] == "REVIEW"
    assert dec["policy"]["requires_confirmation"] is True
    # En estado REVIEW NO se emite capability (Fail closed)
    assert dec["capability"] is None


def test_decision_inspector_four_tabs(client):
    """DOD-06: Endpoint /v1/decisions/{id} suministra las 4 pestañas del inspector."""
    s_resp = client.post("/v1/sessions", json={"goal": "Verificar configuración"})
    session_id = s_resp.json()["data"]["session_id"]

    prop_resp = client.post(f"/v1/sessions/{session_id}/actions", json={
        "tool": "read_file",
        "operation": "pyproject.toml",
        "arguments": {"path": "pyproject.toml"},
    })
    decision_id = prop_resp.json()["data"]["decision_id"]

    # Consultar detalle del inspector
    detail_resp = client.get(f"/v1/decisions/{decision_id}")
    assert detail_resp.status_code == 200
    data = detail_resp.json()["data"]

    # Verificar las 4 pestañas requeridas
    assert "decision_tab" in data
    assert "evidence_tab" in data
    assert "policy_tab" in data
    assert "receipt_tab" in data

    # Inspeccionar contenido de receipt
    assert "nonce" in data["receipt_tab"]
    assert "action_hash" in data["receipt_tab"]
    assert "state_hash" in data["receipt_tab"]
    assert data["receipt_tab"]["has_valid_hmac"] is True


def test_confirm_decision_flow(client):
    """Flujo de confirmación humana: aprobar autoriza y emite capability; rechazar bloquea."""
    s_resp = client.post("/v1/sessions", json={"goal": "Operación sensible"})
    session_id = s_resp.json()["data"]["session_id"]

    prop_resp = client.post(f"/v1/sessions/{session_id}/actions", json={
        "tool": "run_command",
        "arguments": {"command": "git push origin main"},
    })
    decision_id = prop_resp.json()["data"]["decision_id"]
    assert prop_resp.json()["data"]["status"] == "REVIEW"

    # Confirmar y autorizar
    conf_resp = client.post(f"/v1/decisions/{decision_id}/confirm", json={
        "approved": True,
        "reason": "Revisión manual aprobada por DevOps",
        "actor": "admin_adria",
    })
    assert conf_resp.status_code == 200
    conf_data = conf_resp.json()["data"]
    assert conf_data["status"] == "ALLOW"
    assert conf_data["capability"] is not None


def test_execute_decision_in_sandbox(client):
    """DOD-08 y DOD-11: Ejecución autorizada en sandbox vs rechazo de decisión bloqueada."""
    s_resp = client.post("/v1/sessions", json={"goal": "Lectura autorizada"})
    session_id = s_resp.json()["data"]["session_id"]

    # 1. Proponer acción autorizada
    prop_resp = client.post(f"/v1/sessions/{session_id}/actions", json={
        "tool": "read_file",
        "arguments": {"path": "pyproject.toml"},
    })
    decision_id = prop_resp.json()["data"]["decision_id"]
    assert prop_resp.json()["data"]["status"] == "ALLOW"

    # 2. Ejecutar con capability
    exec_resp = client.post(f"/v1/decisions/{decision_id}/execute")
    assert exec_resp.status_code == 200
    exec_data = exec_resp.json()["data"]
    assert exec_data["success"] is True
    assert "praxeon" in exec_data["output"]
    assert exec_data["tier"] == "local_process"

    # 3. Replay attack: intentar volver a ejecutar la misma decisión debe ser denegado por NonceStore
    replay_resp = client.post(f"/v1/decisions/{decision_id}/execute")
    assert replay_resp.status_code == 403


def test_paginated_events_endpoint(client):
    """GET /v1/sessions/{id}/events soporta paginación y gap recovery."""
    s_resp = client.post("/v1/sessions", json={"goal": "Verificar secuencia"})
    session_id = s_resp.json()["data"]["session_id"]

    # Generar acciones
    client.post(f"/v1/sessions/{session_id}/actions", json={"tool": "read_file", "arguments": {"path": "pyproject.toml"}})

    # Recuperar eventos desde secuencia 0
    ev_resp = client.get(f"/v1/sessions/{session_id}/events?after_sequence=0&limit=3")
    assert ev_resp.status_code == 200
    data = ev_resp.json()["data"]
    assert len(data["events"]) <= 3
    assert data["session_id"] == session_id

    # Recuperar la siguiente página usando after_sequence
    last_seq = data["events"][-1]["sequence"]
    next_ev_resp = client.get(f"/v1/sessions/{session_id}/events?after_sequence={last_seq}&limit=10")
    assert next_ev_resp.status_code == 200
    next_data = next_ev_resp.json()["data"]
    assert all(e["sequence"] > last_seq for e in next_data["events"])


def test_websocket_streaming_and_sync(client):
    """DOD-04: Streaming en tiempo real y sincronización gap recovery vía WebSocket."""
    s_resp = client.post("/v1/sessions", json={"goal": "Streaming en vivo"})
    session_id = s_resp.json()["data"]["session_id"]

    with client.websocket_connect(f"/v1/sessions/{session_id}/stream") as ws:
        # 1. Mensaje de bienvenida
        welcome = ws.receive_json()
        assert welcome["action"] == "connected"
        assert welcome["session_id"] == session_id

        # 2. Ping / Pong
        ws.send_json({"action": "ping"})
        pong = ws.receive_json()
        assert pong["action"] == "pong"

        # 3. Proponer acción vía REST simultáneamente y verificar recepción por WebSocket
        client.post(f"/v1/sessions/{session_id}/actions", json={
            "tool": "read_file",
            "arguments": {"path": "pyproject.toml"},
        })

        # Debe recibir eventos de la propuesta
        ev_msg = ws.receive_json()
        assert ev_msg["action"] == "event"
        assert ev_msg["data"]["session_id"] == session_id


def test_frontend_static_serving(client):
    """Verifica que el frontend web compilado se sirve en /."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "PRAXEON" in resp.text


def test_run_mission_interactive_endpoint(client):
    """POST /v1/sessions/run inicializa una misión interactiva y ejecuta pasos en tiempo real."""
    resp = client.post("/v1/sessions/run", json={
        "goal": "Test de misión interactiva",
        "llm_provider": "simulator",
        "supervisor": "laya",
        "max_steps": 3,
        "step_delay_ms": 100,
    })
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert "session_id" in data
    assert data["goal"] == "Test de misión interactiva"
    assert data["status"] == "Active"

    sid = data["session_id"]
    # Comprobar pausa y reanudación
    p_resp = client.post(f"/v1/sessions/{sid}/pause")
    assert p_resp.status_code == 200
    assert p_resp.json()["data"]["status"] == "Paused"

    r_resp = client.post(f"/v1/sessions/{sid}/resume")
    assert r_resp.status_code == 200
    assert r_resp.json()["data"]["status"] == "Resumed"


def test_websocket_handles_datetime_payload(client, test_service):
    """Verifica que el WebSocket serializa correctamente payloads con datetime sin fallar."""
    s_resp = client.post("/v1/sessions", json={"goal": "Test datetime serialization"})
    session_id = s_resp.json()["data"]["session_id"]

    with client.websocket_connect(f"/v1/sessions/{session_id}/stream") as ws:
        welcome = ws.receive_json()
        assert welcome["action"] == "connected"

        # Emitir evento directamente con objetos datetime en el payload
        now_dt = datetime.now(timezone.utc)
        test_service.event_bus.emit(
            session_id=session_id,
            event_type=EventType.CAPABILITY_ISSUED,
            payload={
                "issued_at": now_dt,
                "expires_at": now_dt,
                "nested": {"created": now_dt},
            },
        )

        msg = ws.receive_json()
        assert msg["action"] == "event"
        assert msg["data"]["session_id"] == session_id
        assert msg["data"]["payload"]["issued_at"] == now_dt.isoformat()
        assert msg["data"]["payload"]["nested"]["created"] == now_dt.isoformat()


def test_goal_tailored_step_generator_produces_distinct_trees():
    """Valida que diferentes objetivos generen árboles de decisiones y pensamientos completamente diferenciados."""
    from praxeon.server.dependencies import generate_goal_tailored_steps

    steps_test = generate_goal_tailored_steps("Auditar y ejecutar suite de tests con pytest", max_steps=4)
    steps_auth = generate_goal_tailored_steps("Fix authentication bug in the API and tokens", max_steps=4)
    steps_net = generate_goal_tailored_steps("Inspeccionar contención de red y variables de entorno", max_steps=4)
    steps_custom = generate_goal_tailored_steps("Refactorizar módulo de telemetría y métricas", max_steps=4)

    # 1. Los pensamientos deben ser distintos y hablar del objetivo
    assert "tests" in steps_test[0]["thought"].lower() or "pruebas" in steps_test[0]["thought"].lower()
    assert "autenticación" in steps_auth[0]["thought"].lower() or "auth" in steps_auth[0]["thought"].lower()
    assert "red" in steps_net[0]["thought"].lower() or "contención" in steps_net[0]["thought"].lower()
    assert "telemetría" in steps_custom[0]["thought"].lower() or "refactorizar" in steps_custom[0]["thought"].lower()

    # 2. Las operaciones y herramientas son contextuales
    assert any("pytest" in str(s["arguments"]) for s in steps_test)
    assert any("hmac" in str(s["arguments"]).lower() or "patch" in str(s["arguments"]).lower() for s in steps_auth)
    assert any("isolation" in str(s["arguments"]).lower() or "socket" in str(s["arguments"]).lower() for s in steps_net)
    assert any("telemetría" in str(s) or "telemetria" in str(s) for s in steps_custom)

    # 3. No hay identidad estática entre ellos
    assert steps_test != steps_auth
    assert steps_auth != steps_net
    assert steps_net != steps_custom


def test_mission_with_llm_provider_offline_resilience(client):
    """Verifica que POST /v1/sessions/run con un LLM online sin credenciales active el fallback contextual sin fallar."""
    resp = client.post("/v1/sessions/run", json={
        "goal": "Inspeccionar contención de red y variables de entorno",
        "llm_provider": "groq",
        "supervisor": "laya",
        "max_steps": 2,
        "step_delay_ms": 100,
    })
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["goal"] == "Inspeccionar contención de red y variables de entorno"
    assert data["status"] == "Active"


def test_list_decisions_cross_session_endpoint(client):
    """Verifica que GET /v1/decisions devuelva la lista de decisiones enriquecidas."""
    s_resp = client.post("/v1/sessions", json={"goal": "Auditoría de decisiones cross-session"})
    session_id = s_resp.json()["data"]["session_id"]

    # Proponer una acción para que se genere una decisión
    prop_resp = client.post(f"/v1/sessions/{session_id}/actions", json={
        "tool": "read_file",
        "arguments": {"path": "praxeon/config.py"},
        "description": "Leer configuración del runtime",
    })
    assert prop_resp.status_code == 200

    # Consultar GET /v1/decisions sin session_id (todas)
    dec_all = client.get("/v1/decisions")
    assert dec_all.status_code == 200
    all_items = dec_all.json()["data"]
    assert isinstance(all_items, list)
    assert len(all_items) >= 1
    found = next((d for d in all_items if d["session_id"] == session_id), None)
    assert found is not None
    assert found["tool"] == "read_file"
    assert "praxeon/config.py" in str(found["command"])

    # Consultar GET /v1/decisions con filtro session_id
    dec_filtered = client.get(f"/v1/decisions?session_id={session_id}")
    assert dec_filtered.status_code == 200
    filtered_items = dec_filtered.json()["data"]
    assert all(d["session_id"] == session_id for d in filtered_items)


