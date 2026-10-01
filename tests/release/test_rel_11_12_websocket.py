"""Pruebas de aceptación formal de Release: REL-11 y REL-12 (WebSocket Reconnection, Gap Recovery & Multi-Client Broadcast).

Conforme al documento de cierre 'PRAXEON v1.0.0 — Plan formal de cierre de versión antes de integrar Context Caching':
- REL-11: Reconexión de WebSocket con recuperación exacta de eventos perdidos (after_sequence y comando sync).
- REL-12: Múltiples clientes concurrentes conectados a la misma sesión reciben el broadcast en tiempo real sin desfases,
          y la desconexión de un cliente no degrada a los demás clientes activos ni filtra eventos entre sesiones.
"""

import os
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from praxeon.domain.events import EventType
from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    set_active_security_profile,
    set_runtime_service,
)
from praxeon.server.schemas.action import ProposeActionRequest


VALID_KEY = "praxeon_super_production_secret_key_9999"


@pytest.fixture(autouse=True)
def reset_security_context():
    """Limpia el contexto de seguridad activo antes y después de cada prueba."""
    set_active_security_profile(None)
    yield
    set_active_security_profile(None)


@pytest.fixture
def ws_runtime(tmp_path, monkeypatch):
    """Instancia limpia de RuntimeApplicationService para pruebas de WebSocket."""
    monkeypatch.setenv("PRAXEON_SECRET_KEY", VALID_KEY)
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_KEY)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "ws_rel_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


# =============================================================================
# REL-11: WebSocket Reconnection & Event Gap Recovery
# =============================================================================

def test_rel_11_reconnection_with_after_sequence_parameter(ws_runtime, tmp_path):
    """REL-11: Cliente desconectado se reconecta con after_sequence y recupera exactamente los eventos perdidos."""
    app = create_app()
    client = TestClient(app)
    session_id = "sess_rel_11_gap"

    ws_runtime.create_session(goal="Prueba de gap recovery tras reconexión", session_id=session_id)

    # 1. Emitir eventos 1 y 2
    ev1 = ws_runtime.event_bus.emit(
        session_id=session_id,
        event_type=EventType.ACTION_PROPOSED,
        node_id="node_1",
        payload={"step": 1},
    )
    ev2 = ws_runtime.event_bus.emit(
        session_id=session_id,
        event_type=EventType.RISK_ASSESSED,
        node_id="node_1",
        payload={"step": 1, "risk": "LOW"},
    )

    # 2. Conectar primer WebSocket, verificar estado y simular desconexión
    with client.websocket_connect(f"/v1/sessions/{session_id}/stream?token={VALID_KEY}") as ws1:
        welcome = ws1.receive_json()
        assert welcome["action"] == "connected"
        assert welcome["latest_sequence"] == ev2.sequence
        assert welcome["event_count"] >= 2

    # 3. Emitir eventos 3, 4 y 5 mientras el cliente está desconectado
    ev3 = ws_runtime.event_bus.emit(
        session_id=session_id,
        event_type=EventType.PROVIDER_EVALUATED,
        node_id="node_1",
        payload={"provider": "TypeSafe"},
    )
    ev4 = ws_runtime.event_bus.emit(
        session_id=session_id,
        event_type=EventType.POLICY_DECIDED,
        node_id="node_1",
        payload={"decision": "ALLOW"},
    )
    ev5 = ws_runtime.event_bus.emit(
        session_id=session_id,
        event_type=EventType.EXECUTION_STARTED,
        node_id="node_1",
        payload={"tool": "read_file"},
    )

    # 4. Reconectar con after_sequence=ev2.sequence
    url = f"/v1/sessions/{session_id}/stream?token={VALID_KEY}&after_sequence={ev2.sequence}"
    with client.websocket_connect(url) as ws2:
        welcome2 = ws2.receive_json()
        assert welcome2["action"] == "connected"
        assert welcome2["latest_sequence"] == ev5.sequence

        # Debe recibir exactamente los eventos 3, 4 y 5
        msg3 = ws2.receive_json()
        assert msg3["action"] == "event"
        assert msg3["data"]["sequence"] == ev3.sequence
        assert msg3["data"]["type"] == EventType.PROVIDER_EVALUATED.value

        msg4 = ws2.receive_json()
        assert msg4["action"] == "event"
        assert msg4["data"]["sequence"] == ev4.sequence
        assert msg4["data"]["type"] == EventType.POLICY_DECIDED.value

        msg5 = ws2.receive_json()
        assert msg5["action"] == "event"
        assert msg5["data"]["sequence"] == ev5.sequence
        assert msg5["data"]["type"] == EventType.EXECUTION_STARTED.value


def test_rel_11_interactive_sync_command(ws_runtime):
    """REL-11: Cliente ya conectado solicita eventos perdidos mediante comando interactivo {"action": "sync"}."""
    app = create_app()
    client = TestClient(app)
    session_id = "sess_rel_11_sync_cmd"

    ws_runtime.create_session(goal="Prueba de comando sync", session_id=session_id)

    # Emitir 4 eventos
    for i in range(1, 5):
        ws_runtime.event_bus.emit(
            session_id=session_id,
            event_type=EventType.INTERVENTION_APPLIED,
            node_id=f"node_{i}",
            payload={"step": i},
        )

    # Conectar cliente
    with client.websocket_connect(f"/v1/sessions/{session_id}/stream?token={VALID_KEY}") as ws:
        _ = ws.receive_json()  # Saludo "connected"

        # Solicitar sincronización de eventos posteriores a secuencia 2
        ws.send_json({"action": "sync", "after_sequence": 2})

        ev3 = ws.receive_json()
        assert ev3["action"] == "event"
        assert ev3["data"]["sequence"] == 3

        ev4 = ws.receive_json()
        assert ev4["action"] == "event"
        assert ev4["data"]["sequence"] == 4


def test_rel_11_websocket_ping_pong_liveness(ws_runtime):
    """REL-11: Protocolo de heartbeat / liveness responde inmediatamente con pong."""
    app = create_app()
    client = TestClient(app)
    session_id = "sess_rel_11_ping"

    ws_runtime.create_session(goal="Prueba ping pong", session_id=session_id)

    with client.websocket_connect(f"/v1/sessions/{session_id}/stream?token={VALID_KEY}") as ws:
        _ = ws.receive_json()  # Saludo "connected"

        ws.send_json({"action": "ping"})
        pong = ws.receive_json()
        assert pong["action"] == "pong"


def test_rel_11_reconnection_with_zero_missed_events(ws_runtime):
    """REL-11: Si el cliente se reconecta indicando after_sequence igual al último evento, no recibe duplicados."""
    app = create_app()
    client = TestClient(app)
    session_id = "sess_rel_11_no_gap"

    ws_runtime.create_session(goal="Prueba sin eventos perdidos", session_id=session_id)

    ev1 = ws_runtime.event_bus.emit(
        session_id=session_id,
        event_type=EventType.SESSION_COMPLETED,
        node_id="node_1",
        payload={"status": "done"},
    )

    with client.websocket_connect(f"/v1/sessions/{session_id}/stream?token={VALID_KEY}&after_sequence={ev1.sequence}") as ws:
        welcome = ws.receive_json()
        assert welcome["action"] == "connected"
        assert welcome["latest_sequence"] == ev1.sequence


# =============================================================================
# REL-12: Multi-Client Concurrent Broadcast & Session Isolation
# =============================================================================

def test_rel_12_multi_client_simultaneous_realtime_broadcast(ws_runtime, tmp_path):
    """REL-12: Múltiples clientes conectados simultáneamente reciben el broadcast en tiempo real de forma idéntica."""
    app = create_app()
    client = TestClient(app)
    session_id = "sess_rel_12_multi_client"

    workspace = tmp_path / "ws_multi_client"
    workspace.mkdir(parents=True, exist_ok=True)
    test_f = workspace / "multi.txt"
    test_f.write_text("ok", encoding="utf-8")

    ws_runtime.create_session(
        goal="Sesión multi-cliente para broadcast",
        session_id=session_id,
        workspace_root=str(workspace),
    )

    # Conectar 3 clientes simultáneos a la misma sesión
    with client.websocket_connect(f"/v1/sessions/{session_id}/stream?token={VALID_KEY}") as ws1:
        with client.websocket_connect(f"/v1/sessions/{session_id}/stream?token={VALID_KEY}") as ws2:
            with client.websocket_connect(f"/v1/sessions/{session_id}/stream?token={VALID_KEY}") as ws3:
                # Cada cliente recibe su saludo connected
                assert ws1.receive_json()["action"] == "connected"
                assert ws2.receive_json()["action"] == "connected"
                assert ws3.receive_json()["action"] == "connected"

                # Disparar una propuesta de acción que genera 7 eventos en cascada:
                # 1. ACTION_PROPOSED
                # 2. OPERATION_CLASSIFIED
                # 3. EVIDENCE_EVALUATED
                # 4. RISK_ASSESSED
                # 5. PROVIDER_EVALUATED
                # 6. POLICY_DECIDED
                # 7. CAPABILITY_ISSUED
                prop = ProposeActionRequest(
                    tool="read_file",
                    arguments={"path": str(test_f.resolve())},
                    thought_rationale="Lectura emitida para todos los clientes WebSocket",
                )
                resp = ws_runtime.propose_action(session_id=session_id, proposal=prop)
                assert resp.status == "ALLOW"

                # Leer los 7 eventos en cada uno de los 3 clientes
                expected_types = [
                    EventType.ACTION_PROPOSED.value,
                    EventType.OPERATION_CLASSIFIED.value,
                    EventType.EVIDENCE_EVALUATED.value,
                    EventType.RISK_ASSESSED.value,
                    EventType.PROVIDER_EVALUATED.value,
                    EventType.POLICY_DECIDED.value,
                    EventType.CAPABILITY_ISSUED.value,
                ]

                events_ws1 = [ws1.receive_json()["data"] for _ in range(7)]
                events_ws2 = [ws2.receive_json()["data"] for _ in range(7)]
                events_ws3 = [ws3.receive_json()["data"] for _ in range(7)]

                # Validar tipos
                types_1 = [e["type"] for e in events_ws1]
                types_2 = [e["type"] for e in events_ws2]
                types_3 = [e["type"] for e in events_ws3]

                assert types_1 == expected_types
                assert types_2 == expected_types
                assert types_3 == expected_types

                # Validar secuencias monótonas idénticas en los 3 clientes
                seqs_1 = [e["sequence"] for e in events_ws1]
                seqs_2 = [e["sequence"] for e in events_ws2]
                seqs_3 = [e["sequence"] for e in events_ws3]

                assert seqs_1 == seqs_2 == seqs_3


def test_rel_12_client_disconnect_does_not_affect_remaining_clients(ws_runtime):
    """REL-12: La desconexión abrupta de un cliente no interrumpe el streaming a los clientes restantes."""
    app = create_app()
    client = TestClient(app)
    session_id = "sess_rel_12_disconnect_recovery"

    ws_runtime.create_session(goal="Prueba de desconexión parcial", session_id=session_id)

    with client.websocket_connect(f"/v1/sessions/{session_id}/stream?token={VALID_KEY}") as ws_persist:
        assert ws_persist.receive_json()["action"] == "connected"

        # Conectar y desconectar inmediatamente un segundo cliente efímero
        with client.websocket_connect(f"/v1/sessions/{session_id}/stream?token={VALID_KEY}") as ws_ephemeral:
            assert ws_ephemeral.receive_json()["action"] == "connected"

        # Emitir un evento con ws_ephemeral ya desconectado
        ev = ws_runtime.event_bus.emit(
            session_id=session_id,
            event_type=EventType.INTERVENTION_APPLIED,
            node_id="node_persisted",
            payload={"status": "active"},
        )

        # El cliente persistente debe recibir el evento de forma fluida y sin excepciones
        msg = ws_persist.receive_json()
        assert msg["action"] == "event"
        assert msg["data"]["sequence"] == ev.sequence
        assert msg["data"]["type"] == EventType.INTERVENTION_APPLIED.value


def test_rel_12_isolated_sessions_do_not_leak_cross_session_events(ws_runtime):
    """REL-12: Clientes conectados a sesiones distintas no reciben eventos cruzados (Aislamiento de sesiones)."""
    app = create_app()
    client = TestClient(app)
    session_alpha = "sess_rel_12_alpha"
    session_beta = "sess_rel_12_beta"

    ws_runtime.create_session(goal="Sesión Alpha", session_id=session_alpha)
    ws_runtime.create_session(goal="Sesión Beta", session_id=session_beta)

    with client.websocket_connect(f"/v1/sessions/{session_alpha}/stream?token={VALID_KEY}") as ws_alpha:
        with client.websocket_connect(f"/v1/sessions/{session_beta}/stream?token={VALID_KEY}") as ws_beta:
            assert ws_alpha.receive_json()["action"] == "connected"
            assert ws_beta.receive_json()["action"] == "connected"

            # Emitir evento en Alpha
            ev_alpha = ws_runtime.event_bus.emit(
                session_id=session_alpha,
                event_type=EventType.ACTION_PROPOSED,
                node_id="alpha_node",
                payload={"target": "alpha"},
            )

            # Cliente Alpha debe recibirlo
            msg_alpha = ws_alpha.receive_json()
            assert msg_alpha["action"] == "event"
            assert msg_alpha["data"]["session_id"] == session_alpha

            # Enviar ping a Beta para verificar que su canal sigue vivo y no recibió el evento de Alpha
            ws_beta.send_json({"action": "ping"})
            resp_beta = ws_beta.receive_json()
            assert resp_beta["action"] == "pong", (
                "Cliente Beta debió responder al ping; si hubiese recibido evento de Alpha, el primer mensaje no sería pong."
            )
