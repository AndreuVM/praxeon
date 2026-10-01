"""Pruebas de aceptación formal de Release: REL-10 (Crash Recovery & Nonce/Decision Persistence).

Conforme al documento de cierre 'PRAXEON v1.0.0 — Plan formal de cierre de versión antes de integrar Context Caching':
- REL-10: Caída súbita del servidor tras la emisión de una decisión (destrucción de instancia en memoria sin graceful shutdown).
          Reinicio de nueva instancia apuntando al mismo almacenamiento SQLite (state.db, decisions.db, events.db, nonces.db).
- Verificaciones formales:
  1. Una decisión en REVIEW pendiente previa a la caída se recupera intacta y puede ser confirmada y ejecutada en la nueva instancia.
  2. Una decisión en ALLOW ya ejecutada antes de la caída NO puede re-ejecutarse tras el reinicio (el nonce persiste en disco y bloquea el replay).
  3. Una decisión en BLOCK previa a la caída permanece inmutablemente en BLOCK tras el reinicio y no puede ejecutarse.
  4. Reconstrucción exacta del árbol de decisiones (DecisionTree), hashes de integridad y secuencia monótona de eventos sin corrupción.
"""

from datetime import datetime
import os
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from praxeon.domain.decision import DecisionStatus
from praxeon.runtime.executor import PolicyViolation
from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    set_active_security_profile,
    set_runtime_service,
)
from praxeon.server.schemas.action import ProposeActionRequest
from praxeon.server.schemas.decision import (
    ConfirmDecisionRequest,
    ExecuteDecisionRequest,
)


VALID_KEY = "praxeon_super_production_secret_key_9999"


@pytest.fixture(autouse=True)
def reset_security_context():
    """Limpia el contexto global de seguridad antes y después de cada prueba."""
    set_active_security_profile(None)
    yield
    set_active_security_profile(None)


def test_rel_10_pending_review_decision_survives_crash_and_executes(tmp_path, monkeypatch):
    """REL-10: Una decisión en REVIEW pendiente antes de la caída es recuperada por la nueva instancia, confirmada y ejecutada."""
    monkeypatch.setenv("PRAXEON_SECRET_KEY", VALID_KEY)
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_KEY)

    db_dir = tmp_path / "crash_rel_10_review"
    db_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = tmp_path / "workspace_rel_10_review"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    session_id = "sess_rel_10_review_recovery"

    # =========================================================================
    # INSTANCIA 1: Emitir decisión en REVIEW y simular caída abrupta
    # =========================================================================
    service_1 = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service_1)

    service_1.create_session(
        goal="Sesión de prueba para recuperación de decisiones en REVIEW",
        session_id=session_id,
        workspace_root=str(workspace_dir),
    )

    # Proponer acción con impacto que entra en REVIEW obligatoriamente
    proposal = ProposeActionRequest(
        tool="git",
        operation="push origin main",
        arguments={"command": "push origin main"},
        thought_rationale="Operación que requiere confirmación humana del operador",
    )
    resp_1 = service_1.propose_action(session_id=session_id, proposal=proposal)
    assert resp_1.status == "REVIEW"
    assert resp_1.policy.requires_confirmation is True
    decision_id = resp_1.decision_id

    # Simular caída abrupta: desreferenciar y destruir servicio en memoria sin graceful shutdown
    set_runtime_service(None)
    del service_1

    # =========================================================================
    # INSTANCIA 2: Reiniciar servidor sobre la misma BD y verificar recuperación
    # =========================================================================
    service_2 = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service_2)

    app_2 = create_app()
    client_2 = TestClient(app_2)

    # 1. Consultar la decisión recuperada a través de la API
    get_resp = client_2.get(
        f"/v1/decisions/{decision_id}",
        headers={"X-API-Key": VALID_KEY},
    )
    assert get_resp.status_code == 200
    dec_data = get_resp.json()["data"]
    assert dec_data["decision_id"] == decision_id
    assert dec_data["decision_tab"]["status"] == "REVIEW"
    assert dec_data["session_id"] == session_id

    # 2. El operador humano confirma y autoriza formalmente la decisión en la nueva instancia
    confirm_resp = client_2.post(
        f"/v1/decisions/{decision_id}/confirm",
        headers={"X-API-Key": VALID_KEY},
        json={
            "approved": True,
            "reason": "Autorizado por operador tras reinicio del servidor",
            "operator_id": "operator_sec_lead",
            "role": "operator",
            "operator_token": VALID_KEY,
        },
    )
    assert confirm_resp.status_code == 200
    conf_data = confirm_resp.json()["data"]
    assert conf_data["status"] == "ALLOW"
    assert conf_data["capability"] is not None
    cap_token = conf_data["capability"]

    # 3. Ejecutar la decisión autorizada en la nueva instancia
    exec_resp = client_2.post(
        f"/v1/decisions/{decision_id}/execute",
        headers={"X-API-Key": VALID_KEY},
        json={"capability_token": cap_token, "operator_id": "operator_sec_lead"},
    )
    assert exec_resp.status_code == 200
    exec_data = exec_resp.json()["data"]
    assert exec_data["success"] is True or exec_data.get("output") is not None

    # Limpieza
    set_runtime_service(None)


def test_rel_10_executed_decision_nonce_persists_and_prevents_replay_post_crash(tmp_path, monkeypatch):
    """REL-10: Un capability ya ejecutado en la instancia 1 persiste en SqliteNonceStore e impide el replay en la instancia 2."""
    monkeypatch.setenv("PRAXEON_SECRET_KEY", VALID_KEY)
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_KEY)

    db_dir = tmp_path / "crash_rel_10_nonce"
    db_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = tmp_path / "workspace_rel_10_nonce"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    test_file = workspace_dir / "target.txt"
    test_file.write_text("contenido del objetivo", encoding="utf-8")

    session_id = "sess_rel_10_nonce_persistence"

    # =========================================================================
    # INSTANCIA 1: Ejecutar decisión y consumir capability legítimamente
    # =========================================================================
    service_1 = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service_1)

    service_1.create_session(
        goal="Sesión de prueba para persistencia de nonces",
        session_id=session_id,
        workspace_root=str(workspace_dir),
    )

    prop = ProposeActionRequest(
        tool="read_file",
        arguments={"path": str(test_file.resolve())},
        thought_rationale="Lectura para consumir capability",
    )
    resp_1 = service_1.propose_action(session_id=session_id, proposal=prop)
    assert resp_1.status == "ALLOW"
    assert resp_1.capability is not None

    decision_id = resp_1.decision_id
    cap_token = resp_1.capability

    # Ejecutar en la instancia 1 -> Éxito
    app_1 = create_app()
    client_1 = TestClient(app_1)
    exec_1 = client_1.post(
        f"/v1/decisions/{decision_id}/execute",
        headers={"X-API-Key": VALID_KEY},
        json={"capability_token": cap_token},
    )
    assert exec_1.status_code == 200

    # Simular caída abrupta
    set_runtime_service(None)
    del service_1
    del client_1

    # =========================================================================
    # INSTANCIA 2: Reiniciar servidor e intentar re-ejecutar (Ataque de Replay post-caída)
    # =========================================================================
    service_2 = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service_2)

    app_2 = create_app()
    client_2 = TestClient(app_2)

    # El atacante intenta usar el mismo capability token en la nueva instancia
    replay_attempt = client_2.post(
        f"/v1/decisions/{decision_id}/execute",
        headers={"X-API-Key": VALID_KEY},
        json={"capability_token": cap_token},
    )

    # Debe ser denegado tajantemente con 403 Forbidden
    assert replay_attempt.status_code == 403
    detail = replay_attempt.json()["detail"]
    assert "ya ha sido ejecutada previamente" in detail or "ya ha sido consumido previamente" in detail

    set_runtime_service(None)


def test_rel_10_blocked_decision_remains_blocked_post_crash(tmp_path, monkeypatch):
    """REL-10: Una decisión en BLOCK previa a la caída permanece inmutable en BLOCK en la instancia 2."""
    monkeypatch.setenv("PRAXEON_SECRET_KEY", VALID_KEY)
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_KEY)

    db_dir = tmp_path / "crash_rel_10_block"
    db_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = tmp_path / "workspace_rel_10_block"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    session_id = "sess_rel_10_block_persistence"

    # =========================================================================
    # INSTANCIA 1: Emitir decisión en BLOCK
    # =========================================================================
    service_1 = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service_1)

    service_1.create_session(
        goal="Sesión de prueba de inmutabilidad de BLOCK tras crash",
        session_id=session_id,
        workspace_root=str(workspace_dir),
    )

    destructive_prop = ProposeActionRequest(
        tool="run_shell",
        operation="rm -rf /",
        arguments={"command": "rm -rf /"},
        thought_rationale="Intento destructivo que debe quedar bloqueado permanentemente",
    )
    resp_1 = service_1.propose_action(session_id=session_id, proposal=destructive_prop)
    assert resp_1.status == "BLOCK"
    decision_id = resp_1.decision_id

    # Caída forzada
    set_runtime_service(None)
    del service_1

    # =========================================================================
    # INSTANCIA 2: Reiniciar servidor y verificar inmutabilidad de BLOCK
    # =========================================================================
    service_2 = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service_2)

    app_2 = create_app()
    client_2 = TestClient(app_2)

    # 1. Consultar estado de la decisión en la instancia 2
    get_resp = client_2.get(
        f"/v1/decisions/{decision_id}",
        headers={"X-API-Key": VALID_KEY},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["decision_tab"]["status"] == "BLOCK"

    # 2. Intentar forzar la ejecución en la instancia 2
    exec_attempt = client_2.post(
        f"/v1/decisions/{decision_id}/execute",
        headers={"X-API-Key": VALID_KEY},
        json={},
    )
    assert exec_attempt.status_code == 403
    assert "Solo se permite la ejecución de decisiones 'ALLOW'" in exec_attempt.json()["detail"]

    set_runtime_service(None)


def test_rel_10_decision_tree_and_event_sequence_integrity_post_crash(tmp_path, monkeypatch):
    """REL-10: El árbol de decisiones, el snapshot de sesión y la secuencia monótona de eventos se preservan íntegramente tras el reinicio."""
    monkeypatch.setenv("PRAXEON_SECRET_KEY", VALID_KEY)
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_KEY)

    db_dir = tmp_path / "crash_rel_10_tree"
    db_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = tmp_path / "workspace_rel_10_tree"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    file_a = (workspace_dir / "file_a.txt").resolve()
    file_a.write_text("archivo A", encoding="utf-8")
    file_b = (workspace_dir / "file_b.txt").resolve()
    file_b.write_text("archivo B", encoding="utf-8")

    session_id = "sess_rel_10_tree_recovery"

    # =========================================================================
    # INSTANCIA 1: Generar múltiples transacciones y capturar estado
    # =========================================================================
    service_1 = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service_1)

    service_1.create_session(
        goal="Sesión para verificar árbol de decisiones tras crash",
        session_id=session_id,
        workspace_root=str(workspace_dir),
    )

    proposals = [
        ProposeActionRequest(tool="read_file", arguments={"path": str(file_a)}, thought_rationale="Paso 1"),
        ProposeActionRequest(tool="read_file", arguments={"path": str(file_b)}, thought_rationale="Paso 2"),
        ProposeActionRequest(tool="git", arguments={"command": "status"}, thought_rationale="Paso 3 git"),
    ]

    emitted_ids = []
    for p in proposals:
        r = service_1.propose_action(session_id=session_id, proposal=p)
        emitted_ids.append(r.decision_id)
        if r.status == "ALLOW":
            service_1.execute_decision(decision_id=r.decision_id)

    snapshot_pre_crash = service_1.get_session_snapshot(session_id)
    events_pre_crash = service_1.event_bus.get_all_events(session_id)
    assert len(events_pre_crash) >= 10, "Deben haberse registrado al menos 10 eventos en SQLite"

    # Caída súbita
    set_runtime_service(None)
    del service_1

    # =========================================================================
    # INSTANCIA 2: Reiniciar servidor y verificar paridad exacta
    # =========================================================================
    service_2 = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service_2)

    app_2 = create_app()
    client_2 = TestClient(app_2)

    # 1. Consultar sesión por endpoint REST
    sess_resp = client_2.get(
        f"/v1/sessions/{session_id}",
        headers={"X-API-Key": VALID_KEY},
    )
    assert sess_resp.status_code == 200
    sess_data = sess_resp.json()["data"]

    recovered_tree = sess_data["tree"]
    recovered_summary = sess_data["summary"]

    # 2. Verificar paridad exacta del árbol de decisiones
    assert recovered_summary["total_decisions"] == len(emitted_ids)
    assert recovered_tree["node_count"] == snapshot_pre_crash["tree"]["node_count"]
    assert set(recovered_tree["nodes"].keys()) == set(snapshot_pre_crash["tree"]["nodes"].keys())

    # 3. Continuar operaciones en la sesión recuperada
    file_c = (workspace_dir / "file_c.txt").resolve()
    file_c.write_text("archivo C post-reinicio", encoding="utf-8")
    post_action = ProposeActionRequest(
        tool="read_file",
        arguments={"path": str(file_c)},
        thought_rationale="Paso 4 post-reinicio",
    )
    post_resp = service_2.propose_action(session_id=session_id, proposal=post_action)
    assert post_resp.decision_id not in emitted_ids
    assert post_resp.status in ("ALLOW", "REVIEW")

    # 4. Verificar incremento monótono estricto de eventos sin duplicidad de secuencias
    events_post_crash = service_2.event_bus.get_all_events(session_id)
    assert len(events_post_crash) > len(events_pre_crash)
    # Secuencias estrictamente crecientes
    sequences = [e.sequence for e in events_post_crash]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences)), "No deben existir números de secuencia duplicados"
    assert events_post_crash[-1].sequence > events_pre_crash[-1].sequence

    set_runtime_service(None)
