"""Test suite para REF-02: Descomposición modular de RuntimeApplicationService."""

import pytest
from praxeon.server import (
    RuntimeApplicationService,
    SessionLifecycleCoordinator,
    StepExecutionCoordinator,
    CheckpointCoordinator,
    DiagnosticsCoordinator,
)
from praxeon.server.schemas.action import ProposeActionRequest


@pytest.fixture
def runtime_service(tmp_path):
    svc = RuntimeApplicationService(db_dir=str(tmp_path))
    return svc


def test_coordinators_initialization(runtime_service):
    # 1. Validar instanciación de los 4 coordinadores
    assert isinstance(runtime_service.session_coordinator, SessionLifecycleCoordinator)
    assert isinstance(runtime_service.step_coordinator, StepExecutionCoordinator)
    assert isinstance(runtime_service.checkpoint_coordinator, CheckpointCoordinator)
    assert isinstance(runtime_service.diagnostics_coordinator, DiagnosticsCoordinator)

    # 2. Validar referencia backlink al servicio
    assert runtime_service.session_coordinator.service is runtime_service
    assert runtime_service.step_coordinator.service is runtime_service
    assert runtime_service.checkpoint_coordinator.service is runtime_service
    assert runtime_service.diagnostics_coordinator.service is runtime_service


def test_session_lifecycle_coordinator(runtime_service):
    # Crear sesión vía session_coordinator
    session = runtime_service.session_coordinator.create_session(goal="Evaluar coordinadores modulares")
    sid = session["session_id"]
    assert sid.startswith("s-")

    # Obtener y listar vía session_coordinator y fachada pública
    assert runtime_service.session_coordinator.get_session(sid)["session_id"] == sid
    assert runtime_service.get_session(sid)["session_id"] == sid

    sessions = runtime_service.session_coordinator.list_sessions()
    assert any(s["session_id"] == sid for s in sessions)

    summary = runtime_service.session_coordinator.get_session_summary(sid)
    assert summary["session_id"] == sid
    assert summary["goal"] == "Evaluar coordinadores modulares"


def test_step_execution_coordinator(runtime_service):
    session = runtime_service.create_session(goal="Test de ejecución de pasos")
    sid = session["session_id"]

    req = ProposeActionRequest(
        tool="run_command",
        arguments={"command": "dir"},
        thought_rationale="Listar archivos para auditoría",
    )

    # Proponer acción delegando a través de step_coordinator
    resp = runtime_service.propose_action(session_id=sid, proposal=req)
    assert resp.session_id == sid
    assert resp.status in ("ALLOW", "REVIEW", "BLOCK")

    # Confirmar / rechazar vía step_coordinator
    if resp.status == "REVIEW":
        conf = runtime_service.step_coordinator.confirm_decision(
            decision_id=resp.decision_id,
            approved=True,
            reason="Aprobado en test de coordinadores",
        )
        assert conf.status == "ALLOW"


def test_checkpoint_coordinator(runtime_service):
    session = runtime_service.create_session(goal="Test de rollback y checkpoints")
    sid = session["session_id"]

    # Crear checkpoint atómico
    ckpt = runtime_service.checkpoint_coordinator.create_checkpoint(session_id=sid, label="Punto de control 1")
    assert ckpt["session_id"] == sid
    assert ckpt["label"] == "Punto de control 1"

    # Rollback a través del checkpoint_coordinator y la fachada
    rb_res = runtime_service.rollback_session(session_id=sid, checkpoint_id=ckpt["id"])
    assert rb_res["status"] == "RolledBack"
    assert rb_res["session_id"] == sid


def test_diagnostics_coordinator(runtime_service):
    session = runtime_service.create_session(goal="Test de diagnóstico")
    sid = session["session_id"]

    req = ProposeActionRequest(
        tool="run_command",
        arguments={"command": "echo 'diag'"},
        thought_rationale="Acción diagnóstica",
    )
    resp = runtime_service.propose_action(session_id=sid, proposal=req)

    # Inspección de pestañas a través de diagnostics_coordinator
    detail = runtime_service.diagnostics_coordinator.get_decision_detail(resp.decision_id)
    assert detail is not None
    assert detail.decision_id == resp.decision_id
    assert detail.decision_tab["status"] == resp.status

    # Listar catálogo de proveedores
    catalog = runtime_service.diagnostics_coordinator.get_available_providers()
    assert "decision_providers" in catalog
    assert "llm_providers" in catalog
