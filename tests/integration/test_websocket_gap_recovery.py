"""Pruebas de tolerancia a fallos y recuperación de brechas (Gap Recovery) en WebSocket (P0.5).

Auditoría Técnica PRAXEON Sección 4 y Sección 14:
Escenario de prueba formal:
1. Emitir eventos 1 y 2 en sesión activa.
2. Conectar y forzar cierre de socket de cliente.
3. Emitir eventos 3 y 4 en el backend.
4. Reconectar cliente solicitando after_sequence=2 en query param.
5. Verificar recepción ordenada y exacta de [3, 4] sin duplicados ni pérdidas.
6. Validar soporte del comando interactivo {"action": "sync", "after_sequence": 2}.
7. Validar concurrencia con múltiples clientes reconectando al mismo canal.
"""

import os
import pytest
from fastapi.testclient import TestClient

from praxeon.domain.events import EventType
from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    set_runtime_service,
)


@pytest.fixture
def ws_runtime(tmp_path):
    service = RuntimeApplicationService(db_dir=str(tmp_path / "ws_gap_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


def test_websocket_gap_recovery_after_sequence_query_param(ws_runtime):
    """P0.5: Reconexión tras caída con ?after_sequence=2 entrega exactamente los eventos [3, 4] ordenados."""
    app = create_app()
    client = TestClient(app)
    session_id = "ws_gap_sess_1"

    # 1. Crear sesión
    ws_runtime.create_session(goal="Test WebSocket Gap Recovery", session_id=session_id)
    base_seq = len(ws_runtime.event_bus.get_all_events(session_id))

    # 2. Emitir eventos 1 y 2 en backend
    ev1 = ws_runtime.event_bus.emit(
        session_id=session_id,
        event_type=EventType.ACTION_PROPOSED,
        node_id="node_1",
        payload={"step": 1, "tool": "read_file"},
    )
    ev2 = ws_runtime.event_bus.emit(
        session_id=session_id,
        event_type=EventType.RISK_ASSESSED,
        node_id="node_1",
        payload={"step": 1, "risk_level": "LOW"},
    )
    assert ev1.sequence == base_seq + 1
    assert ev2.sequence == base_seq + 2

    # 3. Conectar primer cliente y validar estado inicial
    with client.websocket_connect(f"/v1/sessions/{session_id}/stream") as ws1:
        welcome = ws1.receive_json()
        assert welcome["action"] == "connected"
        assert welcome["latest_sequence"] == ev2.sequence
        assert welcome["event_count"] == ev2.sequence
        # Forzar cierre del socket desconectando el contexto

    # 4. Emitir eventos 3 y 4 mientras el cliente está desconectado
    ev3 = ws_runtime.event_bus.emit(
        session_id=session_id,
        event_type=EventType.POLICY_DECIDED,
        node_id="node_1",
        payload={"step": 1, "decision": "ALLOW"},
    )
    ev4 = ws_runtime.event_bus.emit(
        session_id=session_id,
        event_type=EventType.EXECUTION_COMPLETED,
        node_id="node_1",
        payload={"step": 1, "output": "File content OK"},
    )
    assert ev3.sequence == base_seq + 3
    assert ev4.sequence == base_seq + 4

    # 5. Reconectar nuevo socket solicitando after_sequence=ev2.sequence
    with client.websocket_connect(f"/v1/sessions/{session_id}/stream?after_sequence={ev2.sequence}") as ws2:
        welcome2 = ws2.receive_json()
        assert welcome2["action"] == "connected"
        assert welcome2["latest_sequence"] == ev4.sequence

        # Recibir catchup gap events
        msg3 = ws2.receive_json()
        assert msg3["action"] == "event"
        assert msg3["data"]["sequence"] == ev3.sequence
        assert msg3["data"]["type"] == EventType.POLICY_DECIDED.value

        msg4 = ws2.receive_json()
        assert msg4["action"] == "event"
        assert msg4["data"]["sequence"] == ev4.sequence
        assert msg4["data"]["type"] == EventType.EXECUTION_COMPLETED.value


def test_websocket_interactive_sync_command(ws_runtime):
    """P0.5: Un cliente conectado puede enviar {"action": "sync", "after_sequence": X} y resincronizar brechas."""
    app = create_app()
    client = TestClient(app)
    session_id = "ws_sync_cmd_sess"

    ws_runtime.create_session(goal="Sync command test", session_id=session_id)

    # Emitir 4 eventos
    for i in range(1, 5):
        ws_runtime.event_bus.emit(
            session_id=session_id,
            event_type=EventType.INTERVENTION_APPLIED,
            node_id=f"node_{i}",
            payload={"index": i},
        )

    # Conectar cliente solicitando inicio desde secuencia 0 (sin gap)
    with client.websocket_connect(f"/v1/sessions/{session_id}/stream") as ws:
        _ = ws.receive_json()  # Mensaje "connected"

        # Enviar comando interactivo de sync solicitando eventos después de secuencia 2
        ws.send_json({"action": "sync", "after_sequence": 2})

        msg_ev3 = ws.receive_json()
        assert msg_ev3["action"] == "event"
        assert msg_ev3["data"]["sequence"] == 3

        msg_ev4 = ws.receive_json()
        assert msg_ev4["action"] == "event"
        assert msg_ev4["data"]["sequence"] == 4


def test_websocket_multiple_concurrent_clients_gap_recovery(ws_runtime):
    """P0.5: Múltiples clientes conectados recuperan brechas concurrentemente sin interferencias de cola."""
    app = create_app()
    client = TestClient(app)
    session_id = "ws_multi_client_sess"

    ws_runtime.create_session(goal="Multi client test", session_id=session_id)

    for i in range(1, 5):
        ws_runtime.event_bus.emit(
            session_id=session_id,
            event_type=EventType.ACTION_PROPOSED,
            node_id=f"act_{i}",
            payload={"step": i},
        )

    # Cliente A solicita after_sequence=1 (debe recibir 2, 3, 4)
    # Cliente B solicita after_sequence=3 (debe recibir sólo 4)
    with client.websocket_connect(f"/v1/sessions/{session_id}/stream?after_sequence=1") as ws_a:
        with client.websocket_connect(f"/v1/sessions/{session_id}/stream?after_sequence=3") as ws_b:
            _ = ws_a.receive_json()
            _ = ws_b.receive_json()

            # Cliente A recibe 2, 3, 4
            a_seqs = [ws_a.receive_json()["data"]["sequence"] for _ in range(3)]
            assert a_seqs == [2, 3, 4]

            # Cliente B recibe sólo 4
            b_ev = ws_b.receive_json()
            assert b_ev["data"]["sequence"] == 4
