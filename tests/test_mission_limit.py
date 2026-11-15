"""Límite de misiones concurrentes (FE-008): PRAXEON_MAX_CONCURRENT_MISSIONS, por defecto 4."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
from praxeon.server.runtime_service.mission_runtime import DEFAULT_MAX_CONCURRENT_MISSIONS, max_concurrent_missions


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key in ("PRAXEON_API_KEY", "PRAXEON_SECRET_KEY", "PRAXEON_PROFILE", "PRAXEON_ENV", "PRAXEON_REQUIRE_AUTH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PRAXEON_MAX_CONCURRENT_MISSIONS", "2")
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    ws = tmp_path / "ws"
    ws.mkdir()
    client = TestClient(create_app(profile="dev"))
    yield service, client, str(ws)
    for sid in list(service._running_missions):
        service.stop_mission(sid)
    deadline = time.time() + 15
    while service.active_mission_count() and time.time() < deadline:
        time.sleep(0.2)
    set_runtime_service(None)


def start(client, ws, sid=None, **extra):
    body = {"goal": "tarea larga", "llm_provider": "simulator", "execution_mode": "local_restricted", "max_steps": 50,
            "step_delay_ms": 60_000, "workspace_root": ws, **extra}
    if sid:
        body["session_id"] = sid
    return client.post("/v1/sessions/run", json=body)


def test_el_limite_se_lee_del_entorno_con_defecto_4(monkeypatch):
    monkeypatch.delenv("PRAXEON_MAX_CONCURRENT_MISSIONS", raising=False)
    assert max_concurrent_missions() == DEFAULT_MAX_CONCURRENT_MISSIONS == 4
    for raw, expected in (("7", 7), ("1", 1), ("0", 4), ("-3", 4), ("muchas", 4), ("", 4), (" 5 ", 5)):
        monkeypatch.setenv("PRAXEON_MAX_CONCURRENT_MISSIONS", raw)
        assert max_concurrent_missions() == expected, raw


def test_la_tercera_mision_se_rechaza_con_un_error_claro_y_sin_crear_nada(env):
    service, client, ws = env
    assert start(client, ws, "m1").status_code == 201
    assert start(client, ws, "m2").status_code == 201
    res = start(client, ws, "m3")
    assert res.status_code == 429
    detail = res.json()["detail"]
    assert "límite es 2" in detail and "PRAXEON_MAX_CONCURRENT_MISSIONS" in detail and "Detén una" in detail
    assert service.active_mission_count() == 2
    ids = {s["session_id"] for s in client.get("/v1/sessions").json()["data"]}
    assert "m3" not in ids                                              # el rechazo no deja una sesión huérfana


def test_al_terminar_una_se_puede_iniciar_otra(env):
    service, client, ws = env
    start(client, ws, "m1")
    start(client, ws, "m2")
    assert start(client, ws, "m3").status_code == 429
    assert service.stop_mission("m1")
    deadline = time.time() + 20
    while service.active_mission_count() > 1 and time.time() < deadline:
        time.sleep(0.2)
    assert service.active_mission_count() == 1
    assert start(client, ws, "m4").status_code == 201


def test_un_arranque_fallido_no_se_queda_con_un_hueco(env):
    service, client, ws = env
    for _ in range(5):                                                  # más intentos fallidos que el límite
        res = start(client, ws, workflow_id="no-existe")
        assert res.status_code == 400
    assert service.active_mission_count() == 0
    assert start(client, ws, "ok1").status_code == 201
    assert start(client, ws, "ok2").status_code == 201


def test_arranques_simultaneos_nunca_superan_el_limite(env):
    service, client, ws = env
    barrier = threading.Barrier(8)
    codes: list[int] = []

    def go(i: int) -> None:
        barrier.wait()
        codes.append(start(client, ws, f"par-{i}").status_code)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join(timeout=60) for t in ts]
    assert sorted(codes) == [201, 201] + [429] * 6
    assert service.active_mission_count() == 2
