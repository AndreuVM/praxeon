"""Pruebas unitarias y de integración para borrado y limpieza de sesiones en PRAXEON (tests/test_session_management.py).

Verifica:
1. Endpoint DELETE /v1/sessions/{session_id} para eliminar sesiones individuales.
2. Comportamiento 404 al intentar eliminar una sesión inexistente.
3. Purga completa de memoria (_sessions_meta), base de datos SQLite (state_store) y eventos (event_bus).
4. Endpoint DELETE /v1/sessions para limpieza masiva de sesiones antiguas y completadas.
5. Respeto del parámetro exclude_session_id para no borrar la sesión en curso durante limpiezas.
6. Detención segura de misiones en ejecución al invocar delete_session.
"""

import time
import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    set_runtime_service,
)


@pytest.fixture
def runtime_service(tmp_path):
    service = RuntimeApplicationService(db_dir=str(tmp_path / "test_session_mgmt_db"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


@pytest.fixture
def client(runtime_service):
    app = create_app()
    return TestClient(app)


def test_delete_individual_session(client, runtime_service):
    """Debe permitir eliminar una sesión existente y purgar todos sus datos."""
    # 1. Crear sesión
    create_resp = client.post("/v1/sessions", json={"goal": "Sesion para borrar"})
    assert create_resp.status_code == 201
    sid = create_resp.json()["data"]["session_id"]

    # Verificar que existe
    get_resp = client.get(f"/v1/sessions/{sid}")
    assert get_resp.status_code == 200

    # 2. Eliminar sesión
    del_resp = client.delete(f"/v1/sessions/{sid}")
    assert del_resp.status_code == 200
    del_data = del_resp.json()["data"]
    assert del_data["session_id"] == sid
    assert del_data["deleted"] is True

    # 3. Verificar que ya no existe (404)
    get_after = client.get(f"/v1/sessions/{sid}")
    assert get_after.status_code == 404

    # 4. Intentar borrarla de nuevo debe retornar 404
    del_again = client.delete(f"/v1/sessions/{sid}")
    assert del_again.status_code == 404


def test_clear_old_sessions_batch(client, runtime_service):
    """Debe permitir purgar sesiones antiguas completadas excluyendo la activa."""
    # Crear 3 sesiones
    resp1 = client.post("/v1/sessions", json={"goal": "Sesion 1 activa"})
    sid1 = resp1.json()["data"]["session_id"]

    resp2 = client.post("/v1/sessions", json={"goal": "Sesion 2 terminada"})
    sid2 = resp2.json()["data"]["session_id"]

    resp3 = client.post("/v1/sessions", json={"goal": "Sesion 3 terminada"})
    sid3 = resp3.json()["data"]["session_id"]

    # Simular que sid2 y sid3 están terminadas/completed
    runtime_service._sessions_meta[sid2]["status"] = "Completed"
    runtime_service._sessions_meta[sid3]["status"] = "Stopped"

    # Purgar antiguas completadas, excluyendo sid2 si fuera activa
    del_batch = client.delete(f"/v1/sessions?only_completed=true&exclude_session_id={sid2}")
    assert del_batch.status_code == 200
    res = del_batch.json()["data"]
    # Debe haber borrado sid3 (Stopped), conservando sid1 (Active) y sid2 (excluida)
    assert res["deleted_count"] >= 1

    # Verificar existencia
    assert client.get(f"/v1/sessions/{sid1}").status_code == 200
    assert client.get(f"/v1/sessions/{sid2}").status_code == 200
    assert client.get(f"/v1/sessions/{sid3}").status_code == 404


def test_delete_running_mission_stops_worker(runtime_service):
    """Eliminar una sesión con misión en ejecución debe detener el worker y purgar el estado."""
    meta = runtime_service.start_mission(
        goal="Misión para eliminar mientras corre",
        max_steps=10,
        step_delay_ms=500,
    )
    sid = meta["session_id"]

    # Verificar que el worker está registrado
    assert sid in runtime_service._running_missions

    # Eliminar sesión
    deleted = runtime_service.delete_session(sid)
    assert deleted is True

    # Verificar que el worker fue detenido y removido
    assert sid not in runtime_service._running_missions
    assert sid not in runtime_service._sessions_meta


def test_post_fallback_deletion(client, runtime_service):
    """Debe permitir eliminar vía POST /{session_id}/delete y POST /v1/sessions/clear."""
    # 1. Crear sesión
    resp = client.post("/v1/sessions", json={"goal": "Sesion para borrar via POST fallback"})
    assert resp.status_code == 201
    sid = resp.json()["data"]["session_id"]

    # 2. Borrar via POST
    del_post = client.post(f"/v1/sessions/{sid}/delete")
    assert del_post.status_code == 200
    assert del_post.json()["data"]["deleted"] is True
    assert client.get(f"/v1/sessions/{sid}").status_code == 404

    # 3. Batch clear via POST /v1/sessions/clear
    resp2 = client.post("/v1/sessions", json={"goal": "Sesion 2 terminada"})
    sid2 = resp2.json()["data"]["session_id"]
    runtime_service._sessions_meta[sid2]["status"] = "Completed"

    clear_post = client.post("/v1/sessions/clear?only_completed=true")
    assert clear_post.status_code == 200
    assert clear_post.json()["data"]["deleted_count"] >= 1
    assert client.get(f"/v1/sessions/{sid2}").status_code == 404

