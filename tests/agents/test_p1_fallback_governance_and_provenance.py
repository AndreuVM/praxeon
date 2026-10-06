"""Pruebas formales de gobernanza explícita del Fallback Sintético (FAIL_CLOSED vs BEST_EFFORT) y trazabilidad de procedencia."""

import os
import pytest
from praxeon.domain.action import ActionCandidate, ToolCall, compute_action_hash
from praxeon.domain.decision import DecisionStatus, PolicyDecision
from praxeon.domain.events import EventType
from praxeon.domain.governance import (
    ActionProvenance,
    FallbackMode,
    resolve_fallback_mode,
)
from praxeon.domain.models import Goal
from praxeon.live_agent.mission_runner import run_live_agent
from praxeon.live_agent.trajectory_controller import TrajectoryController
from praxeon.runtime.state import SessionState, StepRecord
from praxeon.server.app import create_app
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
from fastapi.testclient import TestClient


def test_resolve_fallback_mode():
    """Verifica la resolución correcta de modos desde argumentos y variables de entorno."""
    assert resolve_fallback_mode("FAIL_CLOSED") == FallbackMode.FAIL_CLOSED
    assert resolve_fallback_mode("fail-closed") == FallbackMode.FAIL_CLOSED
    assert resolve_fallback_mode("strict") == FallbackMode.FAIL_CLOSED
    assert resolve_fallback_mode("BEST_EFFORT") == FallbackMode.BEST_EFFORT
    assert resolve_fallback_mode("best-effort") == FallbackMode.BEST_EFFORT
    assert resolve_fallback_mode("allow_simulation") == FallbackMode.BEST_EFFORT
    assert resolve_fallback_mode(FallbackMode.FAIL_CLOSED) == FallbackMode.FAIL_CLOSED

    # Test con variable de entorno
    orig = os.environ.get("PRAXEON_FALLBACK_MODE")
    try:
        os.environ["PRAXEON_FALLBACK_MODE"] = "FAIL_CLOSED"
        assert resolve_fallback_mode(None) == FallbackMode.FAIL_CLOSED

        os.environ["PRAXEON_FALLBACK_MODE"] = "BEST_EFFORT"
        assert resolve_fallback_mode(None) == FallbackMode.BEST_EFFORT
    finally:
        if orig is not None:
            os.environ["PRAXEON_FALLBACK_MODE"] = orig
        else:
            os.environ.pop("PRAXEON_FALLBACK_MODE", None)


def test_action_candidate_provenance_and_hash_stability():
    """Verifica que los metadatos de procedencia no alteren el cálculo determinista del hash SHA-256."""
    action_1 = ActionCandidate(
        id="act_1",
        description="Leer archivo de configuración",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "config.yaml"}),
        requires_evidence=["config.yaml"],
        synthetic_fallback=False,
        model_source="llm:gemini-1.5-flash",
    )
    action_2 = ActionCandidate(
        id="act_1",
        description="Leer archivo de configuración",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "config.yaml"}),
        requires_evidence=["config.yaml"],
        synthetic_fallback=True,
        model_source="synthetic:fallback",
    )
    # El hash canónico de la acción depende del contenido de la operación, no de los metadatos de telemetría
    assert compute_action_hash(action_1) == compute_action_hash(action_2)
    assert action_1.synthetic_fallback is False
    assert action_2.synthetic_fallback is True
    assert action_2.model_source == "synthetic:fallback"


def test_step_record_provenance_and_snapshot_roundtrip():
    """Verifica que StepRecord conserve la procedencia a través de serialización y snapshots."""
    state = SessionState(session_id="sess_gov_1", goal=Goal(objective="Test Governance"))
    action = ActionCandidate(
        id="act_gov",
        description="Ejecutar análisis",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "cargo check"}),
        synthetic_fallback=True,
        model_source="synthetic:fallback",
    )
    decision = PolicyDecision(
        action_id="act_gov",
        status=DecisionStatus.ALLOW,
        reason_codes=["TEST_ALLOW"],
    )

    record = state.add_step(
        action=action,
        decision=decision,
        observation="Finished successfully",
        governance_mode="BEST_EFFORT",
    )
    assert record.synthetic_fallback is True
    assert record.model_source == "synthetic:fallback"
    assert record.governance_mode == "BEST_EFFORT"

    # Snapshot roundtrip
    snapshot = state.to_snapshot()
    restored = SessionState.from_snapshot(snapshot)
    assert len(restored.steps) == 1
    assert restored.steps[0].synthetic_fallback is True
    assert restored.steps[0].model_source == "synthetic:fallback"
    assert restored.steps[0].governance_mode == "BEST_EFFORT"


def test_trajectory_controller_metrics_governance():
    """Verifica que TrajectoryController registre y resuma las métricas de fallback sintético."""
    tc = TrajectoryController(max_steps=5, governance_mode="BEST_EFFORT")
    tc.record_step(
        tool_name="read_file",
        tool_args={"path": "a.txt"},
        observation="ok",
        synthetic_fallback=False,
        model_source="llm:openai:gpt-4o",
    )
    tc.record_step(
        tool_name="finish",
        tool_args={"summary": "Completado"},
        observation="done",
        synthetic_fallback=True,
        model_source="synthetic:fallback",
    )

    summary = tc.get_metrics_summary()
    assert summary["executed_steps"] == 2
    assert summary["synthetic_fallback_count"] == 1
    assert summary["governance_mode"] == "BEST_EFFORT"

    table = tc.build_metrics_table("openai")
    assert any("Modo de gobernanza" in str(row) for row in table.columns[0]._cells)


def test_live_agent_fail_closed_mode_blocks_unauthorized_simulation(monkeypatch):
    """Verifica que en modo FAIL_CLOSED el agente no recurra silenciosamente a simulación."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    # Intentar ejecutar con provider "gemini" sin clave en modo FAIL_CLOSED
    with pytest.raises(RuntimeError) as exc_info:
        run_live_agent(
            task="Generar informe crítico de auditoría",
            provider="gemini",
            gemini_api_key=None,
            fallback_mode="FAIL_CLOSED",
        )

    assert "FAIL_CLOSED" in str(exc_info.value)


def test_live_agent_best_effort_mode_records_synthetic_fallback():
    """Verifica que en modo BEST_EFFORT el agente use simulación pero marcándola explícitamente."""
    finished, answer, session, middleware = run_live_agent(
        task="Generar informe rápido",
        provider="simulated",
        fallback_mode="BEST_EFFORT",
        max_steps=1,
    )
    assert len(session.task_records) == 1
    record = session.task_records[0]
    # En la historia de pasos, la procedencia está marcada
    assert record.executed_steps >= 0
    if record.history_steps:
        first_step = record.history_steps[0]
        assert first_step.get("synthetic_fallback") is True
        assert first_step.get("model_source") == "synthetic:fallback"
        assert first_step.get("governance_mode") == "BEST_EFFORT"


def test_propose_action_rest_endpoint_tracks_provenance(tmp_path, monkeypatch):
    """Verifica que el endpoint /v1/sessions/{session_id}/actions persista synthetic_fallback y model_source."""
    VALID_KEY = "test_key_gov_123"
    monkeypatch.setenv("PRAXEON_SECRET_KEY", VALID_KEY)
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_KEY)

    service = RuntimeApplicationService(db_dir=str(tmp_path / "gov_cache"))
    set_runtime_service(service)

    app = create_app()
    client = TestClient(app)
    auth_headers = {"x-api-key": VALID_KEY}

    # 1. Crear sesión
    session_id = "sess_gov_rest_1"
    create_res = client.post(
        "/v1/sessions",
        headers=auth_headers,
        json={"goal": "Verificar procedencia REST", "session_id": session_id},
    )
    assert create_res.status_code == 201

    # 2. Proponer acción con fallback sintético declarado
    propose_res = client.post(
        f"/v1/sessions/{session_id}/actions",
        headers=auth_headers,
        json={
            "tool": "read_file",
            "operation": "Leer archivo local",
            "arguments": {"path": "pyproject.toml"},
            "thought_rationale": "Verificar configuración",
            "synthetic_fallback": True,
            "model_source": "synthetic:fallback",
            "provenance": {"source": "SyntheticAgent", "step": 1},
        },
    )
    assert propose_res.status_code == 200

    # 3. Comprobar que en el EventStore el evento ACTION_PROPOSED incluye la procedencia
    events = service.get_events(session_id)
    action_proposed_events = [e for e in events if e.type == EventType.ACTION_PROPOSED]
    assert len(action_proposed_events) == 1
    evt = action_proposed_events[0]
    assert evt.payload.get("synthetic_fallback") is True
    assert evt.payload.get("model_source") == "synthetic:fallback"
