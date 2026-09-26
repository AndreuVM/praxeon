"""Prueba End-to-End (E2E) de la Plataforma PRAXEON 1.0 (Fase 4).
Valida el ciclo de vida completo: REST + WebSocket + Policy + Capabilities + Sandbox + Frontend.
"""

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

    # 5. Propuesta de comando de alto riesgo (git push exige confirmación)
    risk_prop = test_app.post(f"/v1/sessions/{session_id}/actions", json={
        "tool": "run_command",
        "operation": "git push origin main",
        "arguments": {"command": "Write-Output 'confirmed_success'"},
        "thought_rationale": "Pushing unreviewed changes to main",
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
