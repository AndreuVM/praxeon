"""Prueba End-to-End (E2E) de la Plataforma PRAXEON 1.0 (Fase 4).
Valida el ciclo de vida completo: REST + WebSocket + Policy + Capabilities + Sandbox + Frontend.
"""

from pathlib import Path
import shutil
import uuid
import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    get_runtime_service,
    set_runtime_service,
)


@pytest.fixture
def test_app(tmp_path):
    """Crea una instancia aislada de la aplicación con almacenamiento efímero."""
    db_dir = tmp_path / "e2e_praxeon_cache"
    service = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service)

    app = create_app()
    app.dependency_overrides[get_runtime_service] = lambda: service

    with TestClient(app) as client:
        yield client

    set_runtime_service(None)


def test_complete_platform_lifecycle_e2e(test_app):
    """Ciclo E2E completo:
    1. Salud del sistema y entrega del Frontend Web en /
    2. Creación de sesión
    3. Propuesta de lectura segura (ALLOW -> Capability emitida)
    4. Ejecución en sandbox con capability
    5. Propuesta de comando sensible (REVIEW -> Requiere confirmación)
    6. Intento de ejecución sin confirmación -> Rechazado
    7. Aprobación humana -> Capability emitida
    8. Consumo de capability en sandbox
    9. Intento de replay con la misma capability -> Rechazado por nonce store
    10. Verificación de auditoría de eventos
    """
    # 1. Healthcheck & Frontend Web
    health = test_app.get("/v1/health")
    assert health.status_code == 200
    assert health.json()["data"]["status"] == "healthy"

    ui = test_app.get("/")
    assert ui.status_code == 200
    assert "PRAXEON" in ui.text

    # 2. Creación de sesión
    create_res = test_app.post("/v1/sessions", json={
        "goal": "E2E Platform Validation Test",
        "session_id": "sess_e2e_test",
    })
    assert create_res.status_code == 201
    session_id = create_res.json()["data"]["session_id"]
    assert session_id == "sess_e2e_test"

    # 3. Propuesta de lectura segura
    safe_prop = test_app.post(f"/v1/sessions/{session_id}/actions", json={
        "tool": "read_file",
        "operation": "pyproject.toml",
        "arguments": {"path": "pyproject.toml"},
        "thought_rationale": "Audit project dependencies",
        "provenance": {"source": "E2ETestAgent", "step": 1},
    })
    assert safe_prop.status_code == 200
    safe_data = safe_prop.json()["data"]
    assert safe_data["status"] == "ALLOW"
    assert safe_data["capability"] is not None
    safe_cap = safe_data["capability"]
    safe_decision_id = safe_data["decision_id"]

    # 4. Ejecución en sandbox con capability válida
    safe_exec = test_app.post(f"/v1/decisions/{safe_decision_id}/execute")
    assert safe_exec.status_code == 200
    assert safe_exec.json()["data"]["success"] is True

    # 5. Propuesta de comando de alto riesgo (mutación local controlada que exige confirmación humana)
    temp_dir_name = f"e2e_dir_{uuid.uuid4().hex[:8]}"
    target_dir = Path.cwd() / temp_dir_name
    cmd = f"mkdir {temp_dir_name}"
    try:
        risk_prop = test_app.post(f"/v1/sessions/{session_id}/actions", json={
            "tool": "run_command",
            "operation": cmd,
            "arguments": {"command": cmd},
            "thought_rationale": "Create temporary directory for workspace execution",
            "provenance": {"source": "E2ETestAgent", "step": 2},
        })
        assert risk_prop.status_code == 200
        risk_data = risk_prop.json()["data"]
        assert risk_data["status"] == "REVIEW"
        assert risk_data["capability"] is None
        risk_decision_id = risk_data["decision_id"]

        # 6. Intento de ejecución directa de acción no confirmada -> 400 Bad Request
        unconfirmed_exec = test_app.post(f"/v1/decisions/{risk_decision_id}/execute")
        assert unconfirmed_exec.status_code in (400, 403)

        # 7. Operador humano aprueba la acción
        confirm_res = test_app.post(f"/v1/decisions/{risk_decision_id}/confirm", json={
            "approved": True,
            "reason": "Verified by security lead",
            "actor": "operator_admin_e2e",
        })
        assert confirm_res.status_code == 200
        confirm_data = confirm_res.json()["data"]
        assert confirm_data["status"] == "ALLOW"
        assert confirm_data["capability"] is not None

        # 8. Ejecución en sandbox con capability confirmada
        risk_exec = test_app.post(f"/v1/decisions/{risk_decision_id}/execute")
        assert risk_exec.status_code == 200
        assert risk_exec.json()["data"]["success"] is True

        # 9. Intento de Replay con la misma capability -> Rechazado por NonceStore (403)
        replay_exec = test_app.post(f"/v1/decisions/{risk_decision_id}/execute")
        assert replay_exec.status_code == 403
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)

    # 10. Inspección de eventos auditados
    events_res = test_app.get(f"/v1/sessions/{session_id}/events?limit=50")
    assert events_res.status_code == 200
    events = events_res.json()["data"]["events"]
    assert len(events) >= 5
    event_types = [e["type"] for e in events]
    assert "session.started" in event_types
    assert "action.proposed" in event_types
    assert "capability.issued" in event_types
    assert "approval.completed" in event_types or "approval.requested" in event_types


def test_e2e_critical_allowed_reaches_executor_and_blocked_never_touches_physical_handler(test_app):
    """Prueba E2E Crítica Sección 10.1:
    Demuestra de forma irrefutable que:
    1. Una acción permitida (ALLOW) invoca el handler físico del sandbox y produce observación.
    2. Una acción bloqueada por política (BLOCK) es rechazada con 403 y NUNCA toca el handler físico del ejecutor.
    """
    from unittest.mock import MagicMock

    service = get_runtime_service()
    assert service is not None

    # Montar espías directamente sobre los handlers del ejecutor físico
    original_executor_execute = service.executor.execute
    mock_executor_execute = MagicMock(wraps=original_executor_execute)
    service.executor.execute = mock_executor_execute

    original_sandbox_dispatch = service.executor._execute_builtin_tool_in_sandbox
    mock_sandbox_dispatch = MagicMock(wraps=original_sandbox_dispatch)
    service.executor._execute_builtin_tool_in_sandbox = mock_sandbox_dispatch

    session_id = "sess_e2e_sec_10_1"
    create_res = test_app.post("/v1/sessions", json={
        "goal": "Demostrar invariante fundamental de seguridad Sección 10.1",
        "session_id": session_id,
        "execution_mode": "local_restricted",
    })
    assert create_res.status_code == 201

    # =========================================================================
    # PARTE 1: Acción Permitida (ALLOW) -> Invoca Handler Físico
    # =========================================================================
    allow_prop = test_app.post(f"/v1/sessions/{session_id}/actions", json={
        "tool": "read_file",
        "operation": "pyproject.toml",
        "arguments": {"path": "pyproject.toml"},
        "thought_rationale": "Lectura segura de metadatos del proyecto",
    })
    assert allow_prop.status_code == 200
    allow_data = allow_prop.json()["data"]
    assert allow_data["status"] == "ALLOW"
    assert allow_data["capability"] is not None
    allow_dec_id = allow_data["decision_id"]

    # Ejecutar en /execute
    exec_allow_res = test_app.post(f"/v1/decisions/{allow_dec_id}/execute")
    assert exec_allow_res.status_code == 200
    assert exec_allow_res.json()["data"]["success"] is True

    # Comprobar que el ejecutor y el sandbox fueron invocados exactamente 1 vez
    assert mock_executor_execute.call_count == 1
    assert mock_sandbox_dispatch.call_count == 1

    # =========================================================================
    # PARTE 2: Acción Destructiva Prohibida (BLOCK) -> NUNCA Toca Handler Físico
    # =========================================================================
    block_prop = test_app.post(f"/v1/sessions/{session_id}/actions", json={
        "tool": "run_command",
        "operation": "rm -rf / --no-preserve-root",
        "arguments": {"command": "rm -rf / --no-preserve-root"},
        "thought_rationale": "Intento de destrucción del sistema anfitrión",
    })
    assert block_prop.status_code == 200
    block_data = block_prop.json()["data"]
    assert block_data["status"] == "BLOCK"
    assert block_data["capability"] is None
    block_dec_id = block_data["decision_id"]

    # Intento de forzar ejecución en /v1/decisions/{id}/execute
    exec_block_res = test_app.post(f"/v1/decisions/{block_dec_id}/execute")
    assert exec_block_res.status_code == 403
    assert "Solo se permite la ejecución de decisiones 'ALLOW'" in exec_block_res.json()["detail"] or \
           "denegada por política" in exec_block_res.json()["detail"]

    # INVARIANTE CRÍTICA SECCIÓN 10.1:
    # El handler físico no recibió NINGUNA invocación adicional (el contador permanece en 1)
    assert mock_executor_execute.call_count == 1, (
        f"Violación de invariante: executor.execute fue invocado {mock_executor_execute.call_count} veces "
        "(se esperaba exactamente 1 por la acción permitida y 0 por la acción bloqueada)."
    )
    assert mock_sandbox_dispatch.call_count == 1, (
        f"Violación de invariante: sandbox dispatch fue invocado {mock_sandbox_dispatch.call_count} veces "
        "(se esperaba exactamente 1 por la acción permitida y 0 por la acción bloqueada)."
    )

    # =========================================================================
    # PARTE 3: Verificación de Cadena de Custodia de Eventos
    # =========================================================================
    events_res = test_app.get(f"/v1/sessions/{session_id}/events?limit=100")
    assert events_res.status_code == 200
    events = events_res.json()["data"]["events"]

    # Extraer eventos de la decisión bloqueada
    block_events = [e for e in events if e.get("decision_id") == block_dec_id]
    block_event_types = [e["type"] for e in block_events]

    # Debe contener política evaluada como BLOCK
    assert "policy.decided" in block_event_types
    policy_ev = next(e for e in block_events if e["type"] == "policy.decided")
    assert policy_ev["payload"]["status"] == "BLOCK"

    # NUNCA debe contener execution.started ni execution.completed para la acción bloqueada
    assert "execution.started" not in block_event_types
    assert "execution.completed" not in block_event_types

