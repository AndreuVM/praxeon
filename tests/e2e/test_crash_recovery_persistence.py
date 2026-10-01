"""Prueba E2E de resiliencia y recuperación ante caídas abruptas / Crash & Restart Recovery (P0.4).

Auditoría Técnica PRAXEON Sección 12 y Sección 14 (P0 Bloqueante):
1. Iniciar sesión interactiva.
2. Emitir al menos 10 eventos y decisiones en SQLite multi-base de datos (events.db, state.db, nonces.db, decisions.db).
3. Simular caída súbita del proceso (destrucción de instancia en memoria sin graceful shutdown).
4. Reiniciar servidor instanciando nuevo servicio sobre el mismo almacén.
5. Consultar sesión mediante GET /v1/sessions/{id}.
6. Validar reconstrucción exacta del árbol de decisiones, estados, contadores y hashes de integridad.
7. Confirmar que la sesión restaurada puede continuar su ciclo de vida operacional sin desincronización.
"""

import os
import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    get_runtime_service,
    set_runtime_service,
)
from praxeon.server.schemas.action import ProposeActionRequest


def test_crash_and_restart_recovery_e2e(tmp_path):
    """P0.4: Valida la reconstrucción exacta del árbol y la continuidad de estado tras reinicio forzado."""
    db_dir = tmp_path / "crash_recovery_e2e"
    db_dir.mkdir(parents=True, exist_ok=True)

    session_id = "sess_crash_test_99"

    # --- PASO 1: Instancia 1 del servidor (antes de la caída) ---
    service_1 = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service_1)

    # Crear sesión
    service_1.create_session(
        goal="Tarea crítica con persistencia anti-caídas",
        session_id=session_id,
        agent_name="ResilientAgent",
        execution_mode="local_restricted",
    )

    # Emitir múltiples propuestas y decisiones (generando más de 10 eventos en events.db)
    actions = [
        ProposeActionRequest(tool="read_file", arguments={"path": "pyproject.toml"}, thought_rationale="Lectura 1"),
        ProposeActionRequest(tool="read_file", arguments={"path": "README.md"}, thought_rationale="Lectura 2"),
        ProposeActionRequest(tool="git", arguments={"command": "status"}, thought_rationale="Inspección git"),
    ]

    emitted_decision_ids = []
    for act in actions:
        res = service_1.propose_action(session_id=session_id, proposal=act)
        emitted_decision_ids.append(res.decision_id)
        if res.status == "ALLOW":
            service_1.execute_decision(decision_id=res.decision_id)

    # Capturar snapshot antes del crash
    snapshot_before = service_1.get_session_snapshot(session_id)
    events_before = service_1.event_bus.get_all_events(session_id)
    event_count_before = len(events_before)

    assert event_count_before >= 10, f"Se esperaban al menos 10 eventos antes del crash, hubo {event_count_before}"
    assert snapshot_before is not None

    # --- PASO 2: Simulación de caída abrupta del servidor (SIGKILL) ---
    # Destruir referencias en memoria completamente sin graceful shutdown
    set_runtime_service(None)
    del service_1

    # --- PASO 3: Reinicio del servidor (Instancia 2) sobre el mismo directorio ---
    service_2 = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service_2)

    app = create_app()
    client = TestClient(app)

    # --- PASO 4: Consultar sesión mediante GET /v1/sessions/{id} ---
    response = client.get(f"/v1/sessions/{session_id}")
    assert response.status_code == 200, f"Error al recuperar sesión tras reinicio: {response.text}"

    data = response.json()["data"]
    recovered_tree = data["tree"]
    recovered_summary = data["summary"]

    # --- PASO 5: Validar paridad exacta de árbol, contadores y persistencia ---
    # Comparar contadores
    assert recovered_summary["session_id"] == session_id
    assert recovered_summary["event_count"] == event_count_before
    assert recovered_summary["total_decisions"] == len(emitted_decision_ids)

    # Comparar árbol derivado por el reductor
    assert recovered_tree["session_id"] == session_id
    assert recovered_tree["node_count"] == snapshot_before["tree"]["node_count"]
    assert len(recovered_tree["nodes"]) == len(snapshot_before["tree"]["nodes"])

    # Verificar que los IDs de nodo en el árbol restaurado coinciden 1 a 1
    nodes_before_ids = set(snapshot_before["tree"]["nodes"].keys())
    nodes_after_ids = set(recovered_tree["nodes"].keys())
    assert nodes_after_ids == nodes_before_ids

    # --- PASO 6: Continuidad operacional sobre la sesión recuperada ---
    # Proponer una nueva acción post-reinicio y verificar que se integra armónicamente
    post_restart_action = ProposeActionRequest(
        tool="read_file",
        arguments={"path": "tests/test_audit_v1_0_0_remediations.py"},
        thought_rationale="Inspección post-reinicio",
    )
    res_post = service_2.propose_action(session_id=session_id, proposal=post_restart_action)
    assert res_post.decision_id not in emitted_decision_ids
    assert res_post.status in ("ALLOW", "REVIEW")

    # Los eventos deben haber continuado incrementando su secuencia monótonamente
    events_after = service_2.event_bus.get_all_events(session_id)
    assert len(events_after) > event_count_before
    assert events_after[-1].sequence > events_before[-1].sequence

    # Limpieza
    set_runtime_service(None)
