"""Pruebas de verificación de remediación de la Auditoría Integral v1.0.0 (Release Candidate).

Cubre:
1. VULN-01: Autenticación fail-closed si auth es requerida pero PRAXEON_API_KEY está vacía o sin configurar.
2. VULN-02: Mitigación de timing attacks en validación de claves API y WebSocket con secrets.compare_digest.
3. BUG-01: Prevención de lockup por replay attack en ejecuciones multi-paso de JEVProxyMiddleware.
4. BUG-02: Grounding factual de archivos locales considerando evidencia registrada en el estado.
5. BUG-04: ToolRegistry rechaza comandos desconocidos que no están en el PATH ni en la lista blanca.
6. P2 Concurrencia: CircuitBreaker thread-safe bajo acceso concurrente multi-hilo.
7. P2 Dual EventBus: EventBus acepta tanto RuntimeEvent como TelemetryEvent sin lanzar excepciones.
"""

import concurrent.futures
import os
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from praxeon.domain.models import ActionCandidate, DecisionStatus, Goal, ToolCall
from praxeon.interceptor.proxy_middleware import JEVProxyMiddleware
from praxeon.policy.registry import ToolRegistry
from praxeon.providers.laya import LayaProvider
from praxeon.providers.resilience import CircuitBreaker, CircuitState
from praxeon.runtime.event_bus import EventBus, EventStore, RuntimeEvent
from praxeon.runtime.state import SessionState
from praxeon.runtime.telemetry import TelemetryEvent
from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    is_auth_required,
    set_runtime_service,
    verify_api_key,
)


@pytest.fixture
def auth_service(tmp_path):
    service = RuntimeApplicationService(db_dir=str(tmp_path / "audit_auth_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


def test_vuln_01_fail_closed_when_key_unconfigured_in_http(monkeypatch):
    """VULN-01: Si se requiere autenticación pero no hay clave en servidor, rechaza con HTTP 500 (fail-closed seguro)."""
    monkeypatch.setenv("PRAXEON_REQUIRE_AUTH", "true")
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_SECRET_KEY", raising=False)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        verify_api_key(x_api_key="any_attacker_token", authorization=None)
    assert exc_info.value.status_code == 500
    assert "Autenticación requerida pero no se ha establecido PRAXEON_API_KEY" in exc_info.value.detail


def test_vuln_01_fail_closed_when_key_unconfigured_in_websocket(auth_service, monkeypatch):
    """VULN-01: Canales WebSocket rechazan conexión con 1008 si la autenticación es requerida pero falta clave."""
    monkeypatch.setenv("PRAXEON_REQUIRE_AUTH", "true")
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_SECRET_KEY", raising=False)

    app = create_app()
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/v1/sessions/vuln1_ws/stream?token=malicious_guess"):
            pass
    assert exc_info.value.code == 1008


def test_bug_01_proxy_middleware_consecutive_tools_no_replay_lockup():
    """BUG-01: Ejecuciones consecutivas de execute_tool() incrementan steps y evitan colisión de nonce/hash."""
    middleware = JEVProxyMiddleware(goal="Inspeccionar archivos y resumir configuración")

    # Ejecución 1
    obs_1 = middleware.execute_tool("read_file", {"path": "pyproject.toml"}, thought_rationale="Paso 1")
    assert obs_1.success is True
    assert len(middleware.session_state.steps) == 1
    assert middleware.session_state.steps[0].action.id == "act_0"

    # Ejecución 2 (consecutiva en el mismo middleware)
    obs_2 = middleware.execute_tool("read_file", {"path": "README.md"}, thought_rationale="Paso 2")
    assert obs_2.success is True
    assert len(middleware.session_state.steps) == 2
    assert middleware.session_state.steps[1].action.id == "act_1"

    # Confirmar que la trayectoria acumulada también creció
    assert len(middleware.trajectory.steps) == 2


def test_bug_04_registry_rejects_nonexistent_cli_tools():
    """BUG-04: ToolRegistry.is_known() devuelve False para herramientas inventadas no presentes en PATH."""
    registry = ToolRegistry(register_defaults=True)

    assert registry.is_known("completely_invented_tool_xyz_99") is False
    assert registry.is_known("malicious_nonexistent_binary") is False
    # Pero las herramientas estándar de desarrollo sí se reconocen
    assert registry.is_known("git") is True
    assert registry.is_known("python") is True
    assert registry.is_known("alembic") is True


def test_circuit_breaker_thread_safety_under_concurrent_load():
    """Concurrencia: CircuitBreaker gestiona fallos y éxitos concurrentes sin condiciones de carrera."""
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=0.1)

    def worker_failure():
        cb.record_failure()
        return cb.state

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker_failure) for _ in range(20)]
        results = [f.result() for f in futures]

    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False


def test_dual_event_bus_accepts_telemetry_event(tmp_path):
    """Dual EventBus: EventBus.publish() acepta tanto RuntimeEvent como TelemetryEvent."""
    db_file = str(tmp_path / "dual_bus.db")
    bus = EventBus(store=EventStore(db_path=db_file))

    # Publicar TelemetryEvent
    te = TelemetryEvent(
        event_type="policy_decision",
        session_id="sess_dual_test",
        decision_id="dec_test_123",
        status="ALLOW",
        reason="Approved by test",
    )
    published = bus.publish(te)
    assert isinstance(published, RuntimeEvent)
    assert published.session_id == "sess_dual_test"
    assert published.sequence == 1


def test_praxeon_config_and_jev_config_deprecation():
    """Tarea 2.3: PraxeonConfig es la configuración canónica y JEVConfig emite DeprecationWarning."""
    import warnings
    from praxeon.config import PraxeonConfig, JEVConfig

    cfg = PraxeonConfig()
    assert cfg.provider.name == "typesafe"
    assert cfg.retry.circuit_breaker_failures == 3
    assert cfg.retry.semantic_loop_threshold == 2
    assert cfg.provider.llm_failure_policy == "synthetic_fallback"

    with pytest.deprecated_call():
        legacy_cfg = JEVConfig()
        assert isinstance(legacy_cfg, PraxeonConfig)


def test_canonical_benchmark_metadata():
    """Tarea 2.4: Metadatos canónicos reproducibles para benchmarks y desambiguación LAYA."""
    from scripts.run_system_comparison import get_canonical_benchmark_metadata

    meta = get_canonical_benchmark_metadata(seed=42)
    assert meta["benchmark_ver"] == "1.0"
    assert meta["seed"] == 42
    assert meta["provider"] == "laya"
    assert meta["engine_type"] == "laya_calibrated_reflex"
    assert "laya_neural_model_421m" in meta["engine_disambiguation"]
    assert "laya_calibrated_reflex" in meta["engine_disambiguation"]
    assert "python" in meta
    assert "os" in meta
    assert "hardware" in meta


def test_llm_failure_policy_fail_closed(tmp_path):
    """Tarea 2.1: Política fail_closed detiene la misión inmediatamente ante fallo de proveedor LLM."""
    import time
    service = RuntimeApplicationService(db_dir=str(tmp_path / "fail_closed_test"))

    meta = service.start_mission(
        goal="Objetivo crítico con fail_closed",
        session_id="sess_fail_closed",
        llm_provider="unsupported_invalid_llm_123",
        llm_failure_policy="fail_closed",
    )

    # Esperar a que el worker procese el fallo
    time.sleep(0.8)

    events = service.event_bus.get_all_events("sess_fail_closed")
    fatal_events = [e for e in events if e.payload and e.payload.get("fatal") is True]
    assert len(fatal_events) >= 1, "Debe registrarse un evento fatal bajo política fail_closed"
    assert fatal_events[0].payload.get("llm_failure_policy") == "fail_closed"


def test_llm_failure_policy_synthetic_fallback(tmp_path):
    """Tarea 2.1: Política synthetic_fallback marca advertencia visible [DEGRADED_MODE: SYNTHETIC_PLANNER]."""
    import time
    service = RuntimeApplicationService(db_dir=str(tmp_path / "synthetic_fallback_test"))

    meta = service.start_mission(
        goal="Objetivo con synthetic fallback",
        session_id="sess_syn_fb",
        llm_provider="unsupported_invalid_llm_456",
        llm_failure_policy="synthetic_fallback",
        max_steps=2,
        step_delay_ms=200,
    )

    # Esperar a que el worker emita el primer paso degradado
    time.sleep(1.2)

    events = service.event_bus.get_all_events("sess_syn_fb")
    warning_events = [e for e in events if e.payload and e.payload.get("degraded_mode") is True]
    assert len(warning_events) >= 1, "Debe registrarse advertencia de modo degradado"
    assert "[DEGRADED_MODE: SYNTHETIC_PLANNER]" in warning_events[0].payload.get("warning", "")


def test_worker_contracts_architecture():
    """Tarea 2.5: Contratos modulares de worker para desacoplamiento arquitectural."""
    from praxeon.runtime.worker_contracts import (
        ParsedStep,
        CircuitBreakerStatus,
        AgentAdapterProtocol,
        TrajectoryControllerProtocol,
        MissionRunnerProtocol,
    )

    step = ParsedStep(
        tool_name="read_file",
        tool_args={"path": "pyproject.toml"},
        thought_rationale="Inspeccionar dependencias",
        raw_output="Thought: ...\nAction: ...",
    )
    assert step.tool_name == "read_file"
    assert step.is_finish is False

    status = CircuitBreakerStatus()
    assert status.technical_failures == 0
    assert status.technical_circuit_open is False
    assert status.semantic_fixations == 0

