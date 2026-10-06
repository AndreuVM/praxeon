"""Pruebas formales de la arquitectura de proveedores y MockProvider desacoplado de dependencias de red."""

import pytest
from praxeon.config import PraxeonConfig
from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.models import ProviderAssessment
from praxeon.domain.models import Goal
from praxeon.providers.base import BaseReasoningProvider, Provider
from praxeon.providers.mock import MockProvider
from praxeon.providers.replay import ReplayProvider
from praxeon.runtime.state import SessionState
from praxeon.server.app import create_app
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
from fastapi.testclient import TestClient


def test_mock_provider_contracts():
    """Verifica que MockProvider satisfaga la interfaz BaseReasoningProvider / Provider."""
    mock = MockProvider()
    assert isinstance(mock, BaseReasoningProvider)
    assert isinstance(mock, Provider)
    assert mock.is_available() is True
    assert mock.name == "mock"
    assert mock.call_count == 0


def test_mock_provider_deterministic_evaluation():
    """Verifica que MockProvider devuelva evaluaciones deterministas y registre llamadas."""
    mock = MockProvider(
        default_confidence=0.92,
        default_grounded_probability=0.98,
        default_loop_probability=0.02,
    )
    state = SessionState(session_id="sess_mock_1", goal=Goal(objective="Test Mock"))
    action = ActionCandidate(
        id="act_1",
        description="Leer archivo de configuración",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "config.yaml"}),
    )

    results = mock.evaluate(state, [action])
    assert len(results) == 1
    assessment = results[0]
    assert assessment.available is True
    assert assessment.confidence == 0.92
    assert assessment.grounded_probability == 0.98
    assert assessment.loop_probability == 0.02
    assert mock.call_count == 1
    assert mock.calls_received[0]["actions"][0].id == "act_1"

    # evaluate_step helper
    step_assessment = mock.evaluate_step(state, action)
    assert step_assessment.confidence == 0.92
    assert mock.call_count == 2


def test_mock_provider_action_and_tool_overrides():
    """Verifica overrides específicos por action_id y por tool_name."""
    mock = MockProvider()
    custom_action_assessment = ProviderAssessment(
        provider="mock",
        available=True,
        confidence=0.99,
        loop_probability=0.95,
        reason_codes=["CUSTOM_LOOP"],
    )
    mock.set_action_assessment("act_override", custom_action_assessment)

    custom_tool_assessment = ProviderAssessment(
        provider="mock",
        available=True,
        confidence=0.88,
        progress_probability=0.99,
        reason_codes=["TOOL_PROGRESS"],
    )
    mock.set_tool_assessment("special_tool", custom_tool_assessment)

    state = SessionState(session_id="sess_mock_2", goal=Goal(objective="Test Overrides"))

    act_1 = ActionCandidate(id="act_override", description="Acción con override explícito")
    act_2 = ActionCandidate(id="act_normal", description="Acción con tool override", tool_call=ToolCall(tool_name="special_tool"))
    act_3 = ActionCandidate(id="act_default", description="Acción estándar", tool_call=ToolCall(tool_name="other_tool"))

    res = mock.evaluate(state, [act_1, act_2, act_3])
    assert len(res) == 3
    assert res[0].reason_codes == ["CUSTOM_LOOP"]
    assert res[1].reason_codes == ["TOOL_PROGRESS"]
    assert res[2].confidence == 0.90  # Default


def test_mock_provider_queued_assessments():
    """Verifica que las evaluaciones encoladas se consuman en orden FIFO."""
    mock = MockProvider()
    a1 = ProviderAssessment(provider="mock", confidence=0.1, reason_codes=["FIRST"])
    a2 = ProviderAssessment(provider="mock", confidence=0.2, reason_codes=["SECOND"])
    mock.queue_assessment(a1)
    mock.queue_assessment(a2)

    state = SessionState(session_id="sess_mock_3", goal=Goal(objective="Test Queue"))
    act = ActionCandidate(id="act_q", description="Acción de cola")

    res1 = mock.evaluate(state, [act])
    assert res1[0].reason_codes == ["FIRST"]

    res2 = mock.evaluate(state, [act])
    assert res2[0].reason_codes == ["SECOND"]

    res3 = mock.evaluate(state, [act])
    assert res3[0].reason_codes == ["DETERMINISTIC_PROGRESS"]


def test_mock_provider_simulated_failure_and_restore():
    """Verifica la simulación de caída de servicio y recuperación."""
    mock = MockProvider()
    mock.simulate_failure("Simulated API 503")
    assert mock.is_available() is False

    state = SessionState(session_id="sess_mock_4", goal=Goal(objective="Test Outage"))
    act = ActionCandidate(id="act_fail", description="Acción durante fallo")

    res = mock.evaluate(state, [act])
    assert len(res) == 1
    assert res[0].available is False
    assert res[0].failure_reason == "Simulated API 503"
    assert "MOCK_OUTAGE" in res[0].reason_codes

    mock.restore()
    assert mock.is_available() is True
    res_restored = mock.evaluate(state, [act])
    assert res_restored[0].available is True


def test_replay_provider_scenario_contracts():
    """Verifica que ReplayProvider sea compatible con la interfaz formal Provider."""
    replay = ReplayProvider(default_scenario="safe_read")
    assert isinstance(replay, BaseReasoningProvider)
    assert replay.is_available is True

    state = SessionState(session_id="sess_replay_1", goal=Goal(objective="Test Replay"))
    act = ActionCandidate(id="act_rep", description="Acción de lectura", tool_call=ToolCall(tool_name="read_file"))

    res = replay.evaluate(state, [act])
    assert len(res) == 1
    assert res[0].available is True
    assert res[0].confidence == 0.90

    # Encolar escenario de bucle
    replay.queue_scenario("loop_detected")
    res_loop = replay.evaluate(state, [act])
    assert res_loop[0].loop_probability == 0.88


def test_runtime_service_with_injected_mock_provider(tmp_path, monkeypatch):
    """Verifica que RuntimeApplicationService acepte MockProvider y procese propuestas REST sin red."""
    VALID_KEY = "test_key_mock_123"
    monkeypatch.setenv("PRAXEON_SECRET_KEY", VALID_KEY)
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_KEY)

    mock = MockProvider(default_confidence=0.97)
    service = RuntimeApplicationService(
        db_dir=str(tmp_path / "mock_cache"),
        provider=mock,
    )
    assert service.provider is mock
    set_runtime_service(service)

    app = create_app()
    client = TestClient(app)
    auth_headers = {"x-api-key": VALID_KEY}

    session_id = "sess_mock_integration"
    create_res = client.post(
        "/v1/sessions",
        headers=auth_headers,
        json={"goal": "Verificar integración con MockProvider", "session_id": session_id},
    )
    assert create_res.status_code == 201

    propose_res = client.post(
        f"/v1/sessions/{session_id}/actions",
        headers=auth_headers,
        json={
            "tool": "read_file",
            "operation": "Leer archivo",
            "arguments": {"path": "README.md"},
            "thought_rationale": "Inspección inicial",
        },
    )
    assert propose_res.status_code == 200
    resp_data = propose_res.json()
    data = resp_data.get("data", resp_data)
    assert data["providers"][0]["name"] == "mock"
    assert data["providers"][0]["score"] == 0.97
    assert mock.call_count == 1


def test_runtime_service_with_config_name_mock(tmp_path, monkeypatch):
    """Verifica que configurar config.provider.name='mock' instancie automáticamente MockProvider."""
    VALID_KEY = "test_key_mock_cfg_123"
    monkeypatch.setenv("PRAXEON_SECRET_KEY", VALID_KEY)
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_KEY)

    cfg = PraxeonConfig.model_validate({"provider": {"name": "mock"}})
    service = RuntimeApplicationService(
        db_dir=str(tmp_path / "mock_cfg_cache"),
        config=cfg,
    )
    assert isinstance(service.provider, MockProvider)
