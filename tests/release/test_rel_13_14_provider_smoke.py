"""Pruebas de aceptación formal de Release: REL-13 y REL-14 (Provider Conformance & Live Agent Smoke).

Conforme al documento de cierre 'PRAXEON v1.0.0 — Plan formal de cierre de versión antes de integrar Context Caching':
- REL-13: Conformidad de Proveedores (Provider Conformance Suite):
          Validación del contrato canónico de ProviderAssessment en TypeSafeAdapter, LayaProvider y ReplayProvider.
          Comportamiento fail-safe ante indisponibilidad (sin credenciales, caídas de red, timeouts) sin excepciones no controladas.
          Máquina de estados de Circuit Breaker (CLOSED -> OPEN -> HALF_OPEN -> CLOSED) y cortocircuito preventivo.
          Detección semántica de bucles, falta de fundamentación y acciones destructivas con primitivas de LAYA.
          Gating de confianza (escalado determinista a ABSTAIN si confidence < min_confidence_threshold).
- REL-14: Smoke Test de Agente en Vivo (Live Agent Smoke Test):
          Misión E2E completa de 3 pasos (Inspección -> Operación fundamentada -> Conclusión terminal 'finish').
          Monotonía secuencial sin gaps en EventStore, reconstrucción 1-a-1 en DecisionTree y no-repetición (nonces consumidos).
          Detección y rechazo de conclusión prematura ('finish' evasivo sin evidencias) hasta recopilar observaciones reales.
          Generación y decodificación de pasos con SimulatedAgentLLM y parse_llm_steps.
"""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import time
from unittest.mock import MagicMock, patch
import pytest
from starlette.testclient import TestClient

from praxeon.agent_llm import SimulatedAgentLLM
from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.assessment import ProviderAssessment
from praxeon.domain.decision import DecisionStatus, PolicyDecision
from praxeon.domain.events import EventType
from praxeon.domain.evidence import Evidence
from praxeon.domain.goal import Goal
from praxeon.live_agent import parse_llm_steps
from praxeon.policy.engine import PolicyEngine
from praxeon.providers.laya import LayaProvider
from praxeon.providers.replay import ReplayProvider
from praxeon.providers.resilience import CircuitBreaker, CircuitState
from praxeon.providers.typesafe import TypeSafeAdapter
from praxeon.runtime.state import SessionState
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
    """Limpia el contexto global de seguridad antes y después de cada prueba."""
    set_active_security_profile(None)
    yield
    set_active_security_profile(None)


@pytest.fixture
def sample_state():
    """Estado de sesión base para pruebas de evaluación semántica."""
    goal = Goal(
        objective="Auditar y validar integridad del sistema",
        success_criteria=["inspección completada", "sin anomalías críticas"],
    )
    state = SessionState(session_id="sess_rel_13_test", goal=goal)
    state.add_evidence(
        Evidence(id="ev_base", claim="archivo_audit_inspeccionado", content_hash="hash_ev_1")
    )
    return state


@pytest.fixture
def sample_action():
    """Acción candidata segura para pruebas de conformidad."""
    return ActionCandidate(
        id="act_inspect",
        description="Leer archivo de configuración para auditoría",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "config.json"}),
        requires_evidence=["archivo_audit_inspeccionado"],
    )


# =============================================================================
# REL-13: Conformidad de Proveedores (Provider Conformance Suite)
# =============================================================================

@pytest.mark.parametrize(
    "provider",
    [
        TypeSafeAdapter(api_key=None),
        LayaProvider(backend="simulated"),
        ReplayProvider(default_scenario="safe_read"),
    ],
)
def test_rel_13_provider_assessment_contract_invariants(provider, sample_state, sample_action):
    """REL-13.1: Todos los proveedores deben satisfacer el contrato canónico de ProviderAssessment sin excepciones."""
    assessments = provider.evaluate(sample_state, [sample_action])
    assert isinstance(assessments, list)
    assert len(assessments) == 1

    ass = assessments[0]
    assert isinstance(ass, ProviderAssessment)
    assert isinstance(ass.available, bool)
    assert isinstance(ass.confidence, (int, float))
    assert 0.0 <= ass.confidence <= 1.0

    if ass.available:
        if ass.progress_probability is not None:
            assert 0.0 <= ass.progress_probability <= 1.0
        if ass.loop_probability is not None:
            assert 0.0 <= ass.loop_probability <= 1.0
        if ass.grounded_probability is not None:
            assert 0.0 <= ass.grounded_probability <= 1.0
        if ass.novelty_probability is not None:
            assert 0.0 <= ass.novelty_probability <= 1.0

    assert isinstance(ass.reason_codes, list)
    assert len(ass.reason_codes) > 0
    assert isinstance(ass.metadata, dict)


def test_rel_13_typesafe_adapter_with_mock_client(sample_state, sample_action):
    """REL-13.2: TypeSafeAdapter decodifica correctamente las preguntas de System One (Choice, Score, Noul)."""
    adapter = TypeSafeAdapter(api_key="mock_key_typesafe")

    mock_client = MagicMock()
    mock_noul = MagicMock()
    mock_noul.prob = 0.05
    mock_choice = MagicMock()
    mock_choice.choice = "none"
    mock_score = MagicMock()
    mock_score.score = 3.6

    mock_client.system_one.return_value = {
        "has_divergence_or_loop": mock_noul,
        "flagged_index": mock_choice,
        "progress_score": mock_score,
    }
    adapter._client = mock_client

    assessments = adapter.evaluate(sample_state, [sample_action])
    assert len(assessments) == 1
    ass = assessments[0]

    assert ass.provider == "typesafe"
    assert ass.available is True
    assert ass.confidence == 0.92
    assert ass.loop_probability == 0.05
    assert ass.grounded_probability == 0.95
    assert ass.progress_probability == 0.90
    assert "GROUNDED_PROGRESS" in ass.reason_codes


def test_rel_13_typesafe_adapter_offline_failsafe(sample_state, sample_action):
    """REL-13.3: TypeSafeAdapter sin API key opera en modo fail-safe reportando API_KEY_MISSING sin lanzar excepciones."""
    adapter = TypeSafeAdapter(api_key=None)
    adapter.api_key = None
    adapter._client = None

    assert adapter.is_available() is False

    assessments = adapter.evaluate(sample_state, [sample_action])
    assert len(assessments) == 1
    ass = assessments[0]

    assert ass.available is False
    assert ass.confidence == 0.0
    assert "API_KEY_MISSING" in ass.reason_codes


def test_rel_13_typesafe_adapter_transient_error_timeout_failsafe(sample_state, sample_action):
    """REL-13.4: Errores de red y timeout en TypeSafeAdapter son capturados limpiamente devolviendo reason_code=TIMEOUT."""
    adapter = TypeSafeAdapter(api_key="mock_key", max_retries=0)

    mock_client = MagicMock()
    mock_client.system_one.side_effect = TimeoutError("HTTP 504 Gateway Timeout")
    adapter._client = mock_client

    assessments = adapter.evaluate(sample_state, [sample_action])
    assert len(assessments) == 1
    ass = assessments[0]

    assert ass.available is False
    assert ass.confidence == 0.0
    assert "TIMEOUT" in ass.reason_codes
    assert "Gateway Timeout" in ass.failure_reason


def test_rel_13_circuit_breaker_state_machine():
    """REL-13.5: Máquina de estados de CircuitBreaker (CLOSED -> OPEN tras fallos -> HALF_OPEN tras timeout -> CLOSED tras éxito)."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.2, half_open_max_trials=1)

    # 1. Estado inicial CLOSED
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True

    # 2. Registrar 2 fallos (por debajo del umbral 3) -> Sigue CLOSED
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True

    # 3. 3er fallo alcanza el umbral -> Transición a OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False

    # 4. Esperar que expire el recovery_timeout -> Transición a HALF_OPEN
    time.sleep(0.25)
    assert cb.state == CircuitState.HALF_OPEN
    # Admite 1 llamada de prueba en HALF_OPEN
    assert cb.allow_request() is True
    # Pruebas subsiguientes en HALF_OPEN son denegadas
    assert cb.allow_request() is False

    # 5. Registro de éxito en HALF_OPEN restaura a CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True

    # 6. Fallo en HALF_OPEN regresa inmediatamente a OPEN
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    time.sleep(0.25)
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_rel_13_typesafe_short_circuit_when_circuit_breaker_open(sample_state, sample_action):
    """REL-13.6: TypeSafeAdapter cortocircuita llamadas inmediatamente sin costo de red cuando el CircuitBreaker está OPEN."""
    cb = CircuitBreaker(failure_threshold=1)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    adapter = TypeSafeAdapter(api_key="mock_key", circuit_breaker=cb)
    mock_client = MagicMock()
    adapter._client = mock_client

    assessments = adapter.evaluate(sample_state, [sample_action])
    assert len(assessments) == 1
    ass = assessments[0]

    assert ass.available is False
    assert ass.confidence == 0.0
    assert "CIRCUIT_BREAKER_OPEN" in ass.reason_codes
    assert mock_client.system_one.call_count == 0


def test_rel_13_laya_hosted_failsafe_on_network_failure(sample_state, sample_action):
    """REL-13.7: LayaProvider con backend hosted captura fallos de conexión HTTP y retorna fail-safe sin excepciones."""
    provider = LayaProvider(
        backend="hosted",
        endpoint_url="http://127.0.0.1:59999/api/infer_nonexistent",
        timeout=0.5,
    )
    assert provider.is_available() is True

    assessment = provider.evaluate_action(sample_state, sample_action)
    assert assessment.available is False
    assert assessment.confidence == 0.0
    assert "LAYA_PROVIDER_UNAVAILABLE" in assessment.reason_codes
    assert "LAYA failure" in assessment.failure_reason


def test_rel_13_laya_loop_detection_primitive(sample_state):
    """REL-13.8: LayaProvider (simulated) detecta bucles reiterados y emite REPLAN con alta loop_probability."""
    provider = LayaProvider(backend="simulated")

    # Añadir 4 pasos idénticos en el historial
    for i in range(4):
        sample_state.add_step(
            action=ActionCandidate(
                id=f"hist_{i}",
                description="Reintentar lectura de log",
                tool_call=ToolCall(tool_name="read_file", arguments={"path": "repeated.log"}),
            ),
            decision=PolicyDecision(status=DecisionStatus.ALLOW),
            observation="sin cambios en el archivo",
        )

    repeated_action = ActionCandidate(
        id="act_loop_candidate",
        description="Reintentar lectura de log por quinta vez",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "repeated.log"}),
    )

    assessment = provider.evaluate_action(sample_state, repeated_action)
    assert assessment.loop_probability >= 0.70
    assert assessment.metadata["choice"]["label"] == "REPLAN"
    assert "LAYA_LOOP_PREVENTED" in assessment.reason_codes


def test_rel_13_laya_ungrounded_evidence_primitive(sample_state):
    """REL-13.9: LayaProvider detecta acciones con premisas no constatadas en la evidencia y emite REPLAN."""
    provider = LayaProvider(backend="simulated")

    ungrounded_action = ActionCandidate(
        id="act_ungrounded",
        description="Modificar tabla con premisas alucinadas",
        tool_call=ToolCall(tool_name="edit_file", arguments={"path": "ghost.sql"}),
        requires_evidence=["tabla_fantasma_verificada_en_produccion"],
    )

    assessment = provider.evaluate_action(sample_state, ungrounded_action)
    assert assessment.grounded_probability <= 0.30
    assert assessment.metadata["choice"]["label"] == "REPLAN"
    assert "LAYA_UNGROUNDED_EVIDENCE" in assessment.reason_codes


def test_rel_13_laya_destructive_action_blocking(sample_state):
    """REL-13.10: LayaProvider detecta comandos destructivos y clasifica la primitiva en BLOCK."""
    provider = LayaProvider(backend="simulated")

    destructive_action = ActionCandidate(
        id="act_destroy",
        description="Borrar toda la raíz del sistema de archivos",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "rm -rf / --no-preserve-root"}),
    )

    assessment = provider.evaluate_action(sample_state, destructive_action)
    assert assessment.metadata["choice"]["label"] == "BLOCK"
    assert "LAYA_DESTRUCTIVE_BLOCK" in assessment.reason_codes


def test_rel_13_policy_gating_escalates_on_low_provider_confidence(sample_action):
    """REL-13.11: PolicyEngine escala deterministamente a ABSTAIN si el juicio del proveedor tiene baja confianza."""
    policy = PolicyEngine(min_confidence_threshold=0.60)

    low_conf_assessment = ProviderAssessment(
        provider="laya",
        available=True,
        confidence=0.35,  # 0.35 < 0.60
        progress_probability=0.70,
        loop_probability=0.10,
        grounded_probability=0.90,
    )

    decision, receipt = policy.evaluate_action(
        action=sample_action,
        state={},
        available_evidence=[Evidence(id="ev_base", claim="archivo_audit_inspeccionado", content_hash="hash_ev_1")],
        provider_assessment=low_conf_assessment,
    )

    assert decision.status == DecisionStatus.ABSTAIN
    assert any("LOW_PROVIDER_CONFIDENCE_ESCALATE" in r for r in decision.reason_codes)
    assert receipt.decision_status == DecisionStatus.ABSTAIN


# =============================================================================
# REL-14: Live Agent Smoke Test
# =============================================================================

def test_rel_14_live_agent_3_step_complete_lifecycle(tmp_path, monkeypatch):
    """REL-14.1: Ciclo de vida completo de un agente ejecutando una misión real de 3 pasos:
    Paso 1: Inspección (read_file de source_data.txt -> ALLOW -> Capability emitida -> Ejecución en sandbox).
    Paso 2: Operación fundamentada (read_file de audit.log -> ALLOW -> Capability emitida -> Ejecución en sandbox).
    Paso 3: Conclusión terminal (finish con resumen detallado -> ALLOW -> Capability emitida -> SESSION_COMPLETED).
    """
    monkeypatch.setenv("PRAXEON_SECRET_KEY", VALID_KEY)
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_KEY)

    db_dir = tmp_path / "agent_smoke_cache"
    workspace_dir = tmp_path / "agent_smoke_workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Crear archivos físicos reales en el workspace
    file_source = workspace_dir / "source_data.txt"
    file_source.write_text("DATA_RECORD_ALPHA: STATUS=VALIDATED HASH=98234", encoding="utf-8")

    file_audit = workspace_dir / "audit.log"
    file_audit.write_text("AUDIT_LOG_2026: INTEGRITY_CHECK=PASSED SIGNATURE=OK", encoding="utf-8")

    service = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service)

    app = create_app()
    client = TestClient(app)
    auth_headers = {"x-api-key": VALID_KEY}

    session_id = "sess_rel_14_live_smoke"
    create_res = client.post(
        "/v1/sessions",
        headers=auth_headers,
        json={
            "goal": "Inspeccionar datos de auditoría, verificar registros y finalizar la misión",
            "session_id": session_id,
            "workspace_root": str(workspace_dir.resolve()),
        },
    )
    assert create_res.status_code == 201

    # =========================================================================
    # PASO 1: Inspección de source_data.txt
    # =========================================================================
    step1_res = client.post(
        f"/v1/sessions/{session_id}/actions",
        headers=auth_headers,
        json={
            "tool": "read_file",
            "operation": f"read_file {file_source.name}",
            "arguments": {"path": str(file_source.resolve())},
            "thought_rationale": "Inspeccionar registros de datos iniciales",
            "provenance": {"source": "AutonomousAgent", "step": 1},
        },
    )
    assert step1_res.status_code == 200
    step1_data = step1_res.json()["data"]
    assert step1_data["status"] == "ALLOW"
    assert step1_data["capability"] is not None
    dec1_id = step1_data["decision_id"]

    # Ejecutar en sandbox
    exec1_res = client.post(f"/v1/decisions/{dec1_id}/execute", headers=auth_headers)
    assert exec1_res.status_code == 200
    exec1_data = exec1_res.json()["data"]
    assert exec1_data["success"] is True
    assert "DATA_RECORD_ALPHA" in exec1_data["output"]

    # =========================================================================
    # PASO 2: Operación fundamentada sobre audit.log
    # =========================================================================
    step2_res = client.post(
        f"/v1/sessions/{session_id}/actions",
        headers=auth_headers,
        json={
            "tool": "read_file",
            "operation": f"read_file {file_audit.name}",
            "arguments": {"path": str(file_audit.resolve())},
            "thought_rationale": "Verificar registros de auditoría correlacionados",
            "provenance": {"source": "AutonomousAgent", "step": 2},
        },
    )
    assert step2_res.status_code == 200
    step2_data = step2_res.json()["data"]
    assert step2_data["status"] == "ALLOW"
    assert step2_data["capability"] is not None
    dec2_id = step2_data["decision_id"]

    # Ejecutar en sandbox
    exec2_res = client.post(f"/v1/decisions/{dec2_id}/execute", headers=auth_headers)
    assert exec2_res.status_code == 200
    exec2_data = exec2_res.json()["data"]
    assert exec2_data["success"] is True
    assert "AUDIT_LOG_2026" in exec2_data["output"]

    # =========================================================================
    # PASO 3: Conclusión terminal (finish)
    # =========================================================================
    step3_res = client.post(
        f"/v1/sessions/{session_id}/actions",
        headers=auth_headers,
        json={
            "tool": "finish",
            "operation": "finish mission",
            "arguments": {
                "summary": "Misión completada con éxito. Se validaron tanto source_data.txt como audit.log con firmas intactas y sin anomalías."
            },
            "thought_rationale": "Misión cumplida, todos los criterios de éxito alcanzados",
            "provenance": {"source": "AutonomousAgent", "step": 3},
        },
    )
    assert step3_res.status_code == 200
    step3_data = step3_res.json()["data"]
    assert step3_data["status"] == "ALLOW"
    assert step3_data["capability"] is not None
    dec3_id = step3_data["decision_id"]

    # Ejecutar conclusión en sandbox
    exec3_res = client.post(f"/v1/decisions/{dec3_id}/execute", headers=auth_headers)
    assert exec3_res.status_code == 200
    exec3_data = exec3_res.json()["data"]
    assert exec3_data["success"] is True

    # =========================================================================
    # VERIFICACIÓN DE ESTADO TERMINAL DE LA SESIÓN
    # =========================================================================
    sess_check = client.get(f"/v1/sessions/{session_id}", headers=auth_headers)
    assert sess_check.status_code == 200
    sess_info = sess_check.json()["data"]
    assert sess_info["status"] == "Completed"


def test_rel_14_agent_trajectory_invariants_and_integrity(tmp_path, monkeypatch):
    """REL-14.2: Invariantes de trayectoria: monotonicidad de secuencia en EventStore, árbol de decisiones y consumo estricto de nonces."""
    monkeypatch.setenv("PRAXEON_SECRET_KEY", VALID_KEY)
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_KEY)

    db_dir = tmp_path / "invariants_smoke_cache"
    workspace_dir = tmp_path / "invariants_smoke_workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    test_file = workspace_dir / "target.txt"
    test_file.write_text("INVARIANT TEST TARGET CONTENT", encoding="utf-8")

    service = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service)

    app = create_app()
    client = TestClient(app)
    auth_headers = {"x-api-key": VALID_KEY}
    session_id = "sess_rel_14_invariants"

    client.post(
        "/v1/sessions",
        headers=auth_headers,
        json={"goal": "Verificar invariantes de trayectoria", "session_id": session_id, "workspace_root": str(workspace_dir.resolve())},
    )

    # Paso 1: Leer
    prop1 = client.post(
        f"/v1/sessions/{session_id}/actions",
        headers=auth_headers,
        json={"tool": "read_file", "arguments": {"path": str(test_file.resolve())}},
    )
    dec1_id = prop1.json()["data"]["decision_id"]
    client.post(f"/v1/decisions/{dec1_id}/execute", headers=auth_headers)

    # Paso 2: Finalizar
    prop2 = client.post(
        f"/v1/sessions/{session_id}/actions",
        headers=auth_headers,
        json={"tool": "finish", "arguments": {"summary": "Objetivo alcanzado y verificado."}},
    )
    dec2_id = prop2.json()["data"]["decision_id"]
    client.post(f"/v1/decisions/{dec2_id}/execute", headers=auth_headers)

    # 1. Monotonicidad de secuencia en EventStore
    events_res = client.get(f"/v1/sessions/{session_id}/events?limit=100", headers=auth_headers)
    assert events_res.status_code == 200
    events = events_res.json()["data"]["events"]
    assert len(events) >= 10

    sequences = [e["sequence"] for e in events]
    assert sequences == list(range(1, len(events) + 1)), "La secuencia de eventos debe ser estrictamente monótona creciente [1..N]"

    # 2. Tipos canónicos presentes en orden
    event_types = [e["type"] for e in events]
    assert "session.started" in event_types
    assert "goal.created" in event_types
    assert "action.proposed" in event_types
    assert "policy.decided" in event_types
    assert "capability.issued" in event_types
    assert "execution.started" in event_types
    assert "execution.completed" in event_types
    assert "session.completed" in event_types

    # 3. Consumo estricto de nonces (Replay bloqueado con 403 Forbidden)
    replay1 = client.post(f"/v1/decisions/{dec1_id}/execute", headers=auth_headers)
    assert replay1.status_code == 403
    replay2 = client.post(f"/v1/decisions/{dec2_id}/execute", headers=auth_headers)
    assert replay2.status_code == 403

    # 4. Árbol de decisiones y Decision Inspector recuperan ambos pasos intactos
    detail1 = client.get(f"/v1/decisions/{dec1_id}", headers=auth_headers)
    assert detail1.status_code == 200
    assert detail1.json()["data"]["decision_tab"]["status"] == "ALLOW"

    detail2 = client.get(f"/v1/decisions/{dec2_id}", headers=auth_headers)
    assert detail2.status_code == 200
    assert detail2.json()["data"]["decision_tab"]["status"] == "ALLOW"


def test_rel_14_premature_finish_rejected_until_evidence_gathered(tmp_path, monkeypatch):
    """REL-14.3: Conclusión prematura ('finish' sin evidencias en tarea investigativa) es rechazada con REPLAN hasta recopilar observaciones."""
    monkeypatch.setenv("PRAXEON_SECRET_KEY", VALID_KEY)
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_KEY)

    db_dir = tmp_path / "premature_cache"
    workspace_dir = tmp_path / "premature_workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    report_file = workspace_dir / "system_specs.txt"
    report_file.write_text("SISTEMA_V1_STATUS: 100% OPERATIVO SIN FALLOS DETECTADOS", encoding="utf-8")

    service = RuntimeApplicationService(db_dir=str(db_dir))
    set_runtime_service(service)

    app = create_app()
    client = TestClient(app)
    auth_headers = {"x-api-key": VALID_KEY}
    session_id = "sess_rel_14_premature"

    # Meta de tipo investigación
    client.post(
        "/v1/sessions",
        headers=auth_headers,
        json={
            "goal": "Analiza e investiga los registros de auditoría y resume hallazgos",
            "session_id": session_id,
            "workspace_root": str(workspace_dir.resolve()),
        },
    )

    # 1. Intento prematuro de conclusión en el paso 1 sin haber leído ningún archivo ni acumulado observaciones
    premature_res = client.post(
        f"/v1/sessions/{session_id}/actions",
        headers=auth_headers,
        json={
            "tool": "finish",
            "operation": "finish",
            "arguments": {"summary": "Todo bien"},
            "thought_rationale": "Intento de terminar sin investigar",
        },
    )
    assert premature_res.status_code == 200
    premature_data = premature_res.json()["data"]
    # Debe ser denegado con REPLAN y no emitir capability
    assert premature_data["status"] == "REPLAN"
    assert premature_data["capability"] is None

    # 2. El agente realiza la investigación necesaria
    read_res = client.post(
        f"/v1/sessions/{session_id}/actions",
        headers=auth_headers,
        json={
            "tool": "read_file",
            "arguments": {"path": str(report_file.resolve())},
            "thought_rationale": "Investigar especificaciones del sistema",
        },
    )
    assert read_res.status_code == 200
    read_data = read_res.json()["data"]
    assert read_data["status"] == "ALLOW"
    dec_read_id = read_data["decision_id"]

    exec_read = client.post(f"/v1/decisions/{dec_read_id}/execute", headers=auth_headers)
    assert exec_read.status_code == 200
    assert exec_read.json()["data"]["success"] is True

    # 3. Tras recopilar la observación, ahora la conclusión detallada es aceptada
    finish_res = client.post(
        f"/v1/sessions/{session_id}/actions",
        headers=auth_headers,
        json={
            "tool": "finish",
            "arguments": {
                "summary": "Investigación completada: se verificó system_specs.txt demostrando que el sistema opera al 100% sin fallos."
            },
            "thought_rationale": "Conclusión fundamentada en evidencias reales recopiladas",
        },
    )
    assert finish_res.status_code == 200
    finish_data = finish_res.json()["data"]
    assert finish_data["status"] == "ALLOW"
    assert finish_data["capability"] is not None

    exec_finish = client.post(f"/v1/decisions/{finish_data['decision_id']}/execute", headers=auth_headers)
    assert exec_finish.status_code == 200

    sess_check = client.get(f"/v1/sessions/{session_id}", headers=auth_headers)
    assert sess_check.json()["data"]["status"] == "Completed"


def test_rel_14_simulated_agent_llm_step_generation():
    """REL-14.4: SimulatedAgentLLM genera secuencias deterministas de Thought + Action decodificables por parse_llm_steps."""
    sim = SimulatedAgentLLM(model_name="sim-smoke-v1")
    assert sim.provider_name == "simulator"

    # Paso 1: Ejecución de comando
    out1 = sim.generate([{"role": "user", "content": "Iniciar auditoría"}])
    assert "Thought:" in out1
    assert "Action:" in out1
    steps1 = parse_llm_steps(out1)
    assert len(steps1) == 1
    assert steps1[0]["tool_name"] == "run_command"

    # Paso 2: Lectura de archivo
    out2 = sim.generate([{"role": "user", "content": "Comando exitoso. Continuar"}])
    steps2 = parse_llm_steps(out2)
    assert len(steps2) == 1
    assert steps2[0]["tool_name"] == "read_file"

    # Paso 3: Conclusión terminal
    out3 = sim.generate([{"role": "user", "content": "Lectura exitosa. Finalizar"}])
    steps3 = parse_llm_steps(out3)
    assert len(steps3) == 1
    assert steps3[0]["tool_name"] == "finish"
