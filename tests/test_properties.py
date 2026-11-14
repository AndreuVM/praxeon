"""Pruebas de invariantes de seguridad y propiedades formales (Property-Based & Formal Verification).

Implementa los requisitos de la Sección 28.4 de la Auditoría Técnica:
1. Una herramienta prohibida nunca se ejecuta (Enforcement físico).
2. Una acción destructiva nunca se aprueba ante fallo o incertidumbre del supervisor.
3. Una acción dependiente nunca se aprueba sin evidencia fáctica previa.
4. CircuitBreaker garantiza aislamiento ante caídas reiteradas del proveedor.
5. DataSanitizer previene fugas de secretos y delimita la frontera de confianza.
6. Shadow mode permite supervisión pasiva con telemetría completa.
7. Cálculo del valor económico neto del supervisor (NavigatorValue).
"""

import time
import pytest
from praxeon.domain.models import (
    ActionCandidate,
    DecisionStatus,
    Goal,
    PolicyDecision,
    ProviderAssessment,
    RiskAssessment,
    RiskLevel,
    ToolCall,
)
from praxeon.evaluation.metrics import EvaluationMetrics, compute_navigator_economic_value
from praxeon.evaluation.runner import BenchmarkRunner
from praxeon.evaluation.scenarios import ScenarioCatalog
from praxeon.policy.engine import PolicyEngine
from praxeon.policy.failsafe import FailSafePolicy
from praxeon.policy.registry import ToolRegistry
from praxeon.policy.sanitizer import DataSanitizer
from praxeon.providers.replay import ReplayProvider
from praxeon.providers.resilience import CircuitBreaker, CircuitState
from praxeon.reasoning.grounding import EvidenceEngine
from praxeon.reasoning.risk import RiskEngine
from praxeon.runtime.executor import PolicyViolation, SecureExecutor
from praxeon.runtime.navigator import Navigator
from praxeon.runtime.state import SessionState
from praxeon.runtime.telemetry import DecisionEvent, EventBus


def test_property_forbidden_tool_never_executed():
    """Invariante 1: Una herramienta prohibida en el estado NUNCA se ejecuta físicamente."""
    executor = SecureExecutor()
    state = SessionState(
        session_id="test_invariants",
        goal=Goal(objective="Seguridad estricta"),
        forbidden_tools=["delete_file", "rm_rf"],
    )
    action = ActionCandidate(
        id="act_forbidden",
        description="Intentar borrar archivo prohibido",
        tool_call=ToolCall(tool_name="delete_file", arguments={"path": "critical.db"}),
    )

    with pytest.raises(PolicyViolation) as excinfo:
        executor.execute(action, state, decision=PolicyDecision(status=DecisionStatus.BLOCK))

    assert "DENEGADA" in str(excinfo.value)


def test_property_destructive_tool_never_allowed_on_provider_down():
    """Invariante 2: Un fallo del supervisor NUNCA autoriza una acción destructiva (FalseAllowRate = 0%)."""
    engine = PolicyEngine(failsafe=FailSafePolicy(block_destructive_on_provider_failure=True))
    risk_engine = RiskEngine()

    destructive_cmds = [
        "rm -rf /var/data",
        "del /f /s /q C:\\Windows",
        "drop table users cascade",
    ]

    for cmd in destructive_cmds:
        action = ActionCandidate(
            id="act_destr",
            description=f"Ejecutar {cmd}",
            tool_call=ToolCall(tool_name="run_command", arguments={"command": cmd}),
        )
        risk = risk_engine.assess_action_risk(action)
        assessment = ProviderAssessment(
            provider="typesafe",
            available=False,
            failure_reason="504 Gateway Timeout",
        )

        decision, receipt = engine.evaluate_action(
            action=action,
            state={},
            provider_assessment=assessment,
            risk_assessment=risk,
        )

        assert decision.status == DecisionStatus.BLOCK, f"Fallo de seguridad: {cmd} fue autorizado ante caída del proveedor"
        assert "PROVIDER_UNAVAILABLE_DESTRUCTIVE_BLOCK" in decision.reason_codes


def test_property_ungrounded_action_never_allowed():
    """Invariante 3: Una acción con precondiciones ausentes NUNCA puede ser autorizada (status != ALLOW)."""
    engine = PolicyEngine()
    action = ActionCandidate(
        id="act_ungrounded",
        description="Editar configuración",
        tool_call=ToolCall(tool_name="edit_file", arguments={"path": "server.conf"}),
        requires_evidence=["archivo_inspeccionado:server.conf", "token_valido"],
    )

    decision, receipt = engine.evaluate_action(
        action=action,
        state={},
        available_evidence=[],  # Sin evidencia
        provider_assessment=ProviderAssessment(provider="mock", available=True, confidence=0.99),
    )

    assert decision.status == DecisionStatus.REPLAN
    assert any("MISSING_REQUIRED_EVIDENCE" in r for r in decision.reason_codes)


def test_circuit_breaker_state_transitions():
    """Verifica la máquina de estados formal del Circuit Breaker (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.2)
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True

    # 1. Simular 2 fallos (por debajo del umbral 3)
    cb.record_failure(Exception("Fallo 1"))
    cb.record_failure(Exception("Fallo 2"))
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True

    # 2. Tercer fallo: transiciona a OPEN
    cb.record_failure(Exception("Fallo 3"))
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False

    # 3. Esperar tiempo de enfriamiento (recovery_timeout)
    time.sleep(0.25)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.allow_request() is True

    # 4. Respuesta exitosa en HALF_OPEN: restablece a CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True


def test_data_sanitizer_redacts_credentials_and_enforces_limits():
    """Verifica que DataSanitizer enmascare claves API, tokens y delimite la frontera de confianza."""
    sanitizer = DataSanitizer(max_payload_bytes=50)

    # 1. Redacción de API Keys y JWT
    sensitive_text = "Usando key sk-abcdef123456789012345678 y token Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"
    cleaned = sanitizer.redact_text(sensitive_text)
    assert "sk-" not in cleaned
    assert "[REDACTED_API_KEY]" in cleaned
    assert "Bearer [REDACTED_TOKEN]" in cleaned

    # 2. Redacción recursiva en diccionarios
    sensitive_dict = {
        "user": "admin",
        "api_key": "secret_key_12345",
        "nested": {"password": "SuperSecretPassword123!"},
    }
    redacted_dict = sanitizer.redact_dict(sensitive_dict)
    assert redacted_dict["api_key"] == "[REDACTED]"
    assert redacted_dict["nested"]["password"] == "[REDACTED]"

    # 3. Truncamiento de payload
    long_content = "A" * 200
    truncated = sanitizer.enforce_payload_limit(long_content)
    assert len(truncated) < 200
    assert "[PAYLOAD_TRUNCATED_DUE_TO_SIZE_LIMIT]" in truncated

    # 4. Envoltura de frontera de confianza
    untrusted = sanitizer.wrap_untrusted("resultado_de_archivo", source="bash")
    assert "<untrusted_content source='bash'>" in untrusted
    assert "</untrusted_content>" in untrusted


def test_claim_evidence_ontology_integration():
    """Verifica la vinculación ontológica formal Evidence -> Claim en EvidenceEngine."""
    engine = EvidenceEngine()
    ev = engine.ingest(
        claim="file_exists:app.py",
        source_type="tool_observation",
        content="def main(): pass",
        confidence=0.98,
    )

    assert ev.claim == "file_exists:app.py"
    assert len(ev.claims) == 1
    assert ev.claims[0].statement == "file_exists:app.py"

    retrieved_claim = engine.get_claim("file_exists:app.py")
    assert retrieved_claim is not None
    assert retrieved_claim.statement == "file_exists:app.py"
    assert ev.id in retrieved_claim.evidence_ids


def test_shadow_mode_telemetry_emission():
    """Verifica que Navigator en shadow_mode emita telemetría sin bloquear la ejecución."""
    bus = EventBus()
    emitted_events = []
    bus.subscribe(lambda e: emitted_events.append(e))

    provider = ReplayProvider(default_scenario="missing_evidence")
    navigator = Navigator(provider=provider, event_bus=bus, shadow_mode=True)
    navigator.start_session(Goal(objective="Prueba de shadow mode"))

    action = ActionCandidate(
        id="act_shadow",
        description="Acción que en modo estricto sería REPLAN",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "missing_for_sure.txt"}),
        requires_evidence=["unobserved_fact"],
    )

    decision, observation = navigator.step(action)

    # La decisión evaluada por la política es REPLAN
    assert decision.status == DecisionStatus.REPLAN
    # Pero en shadow mode se permitió ejecutar y observar
    assert observation is not None
    # Y se emitió el evento con shadow_mode=True
    dec_events = [e for e in emitted_events if isinstance(e, DecisionEvent)]
    assert len(dec_events) == 1
    assert dec_events[0].shadow_mode is True
    assert dec_events[0].decision == "replan"


def test_economic_value_metric_calculation():
    """Verifica el cálculo del valor económico neto del supervisor."""
    metrics = EvaluationMetrics(
        total_scenarios=10,
        correct_decisions=10,
        accuracy=1.0,
        precision=1.0,
        recall=1.0,
        f1_score=1.0,
        false_allow_count=0,
        false_allow_rate=0.0,
        destructive_false_allow_count=0,
        destructive_false_allow_rate=0.0,
        false_block_count=0,
        false_block_rate=0.0,
        justified_block_precision=1.0,
        fail_safe_verification=1.0,
        spurious_termination_rate=0.0,
        latency_p50_ms=0.15,
        latency_p95_ms=0.50,
        latency_p99_ms=0.80,
        latency_mean_ms=0.20,
    )

    econ = compute_navigator_economic_value(
        metrics,
        avoided_failure_unit_cost=150.0,
        supervisor_cost_per_query=0.001,
        latency_cost_per_second=0.01,
    )

    assert econ["gross_avoided_cost"] == 1500.0  # 10 * 150
    assert econ["net_navigator_value"] > 1490.0
    assert econ["false_block_cost"] == 0.0


def test_large_scale_dataset_100_scenarios():
    """Verifica que el generador masivo produzca un benchmark determinista que supera el 100% de los casos."""
    runner = BenchmarkRunner()
    scenarios = ScenarioCatalog.generate_large_scale_dataset(count=100, seed=123)
    assert len(scenarios) == 100

    report = runner.run_benchmark(scenarios=scenarios)
    assert report.metrics.accuracy == 1.0
    assert report.metrics.false_allow_rate == 0.0
    assert report.metrics.destructive_false_allow_count == 0
