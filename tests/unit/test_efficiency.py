"""Pruebas unitarias para el motor de eficiencia y telemetría de PRAXEON (Fase 1).

Valida:
- ModelPricing y catálogo de precios.
- StepEfficiencyRecord y SessionEfficiencySummary.
- EfficiencyCalculator: CRR, DO, ER, CPST.
- Integración en Navigator y emisión reactiva de EfficiencyStepEvent.
"""

import pytest
from unittest.mock import MagicMock

from praxeon.domain.models import (
    ActionCandidate,
    DecisionStatus,
    Goal,
    PolicyDecision,
    ProviderAssessment,
    ToolCall,
)
from praxeon.evaluation.efficiency import (
    EfficiencyCalculator,
    EfficiencyMetrics,
    MODEL_PRICING_CATALOG,
    ModelPricing,
    SessionEfficiencySummary,
    StepEfficiencyRecord,
)
from praxeon.runtime.executor import SecureExecutor, ToolObservation
from praxeon.runtime.navigator import Navigator
from praxeon.runtime.telemetry import EfficiencyStepEvent, EventBus


def test_model_pricing_catalog():
    """Valida la presencia y consistencia de los modelos canónicos en el catálogo de precios."""
    assert "deepseek-r1-7b" in MODEL_PRICING_CATALOG
    assert "gpt-4o" in MODEL_PRICING_CATALOG
    assert "claude-3-5-sonnet" in MODEL_PRICING_CATALOG
    assert "llama-3-8b" in MODEL_PRICING_CATALOG
    assert "typesafe-local" in MODEL_PRICING_CATALOG

    p = MODEL_PRICING_CATALOG["deepseek-r1-7b"]
    assert p.price_per_million_input_tokens == 0.55
    assert p.price_per_million_output_tokens == 2.19
    assert p.supervisor_cost_per_decision > 0.0


def test_calculate_step_cost():
    """Verifica el cálculo de coste en USD por paso según volumen de tokens."""
    # 10,000 in, 1,000 out en deepseek-r1-7b
    cost = EfficiencyCalculator.calculate_step_cost(10_000, 1_000, "deepseek-r1-7b")
    # cost = (10_000 / 1M * 0.55) + (1_000 / 1M * 2.19) + 0.0005 = 0.0055 + 0.00219 + 0.0005 = 0.00819
    assert pytest.approx(cost, rel=1e-3) == 0.00819


def test_session_aggregation():
    """Verifica la agregación formal de múltiples pasos en un SessionEfficiencySummary."""
    steps = [
        StepEfficiencyRecord(
            step_index=0,
            action_id="act_1",
            tool_name="read_file",
            decision_status="ALLOW",
            tokens_in=500,
            tokens_out=100,
            tokens_total=600,
            llm_calls=1,
            llm_latency_ms=250.0,
            praxeon_overhead_ms=5.0,
            execution_time_ms=10.0,
            total_step_latency_ms=265.0,
            physical_execution_attempted=True,
            physical_execution_allowed=True,
            physical_execution_success=True,
            is_error=False,
            cost_usd=0.001,
        ),
        StepEfficiencyRecord(
            step_index=1,
            action_id="act_2",
            tool_name="bash_command",
            decision_status="BLOCK",
            tokens_in=600,
            tokens_out=50,
            tokens_total=650,
            llm_calls=1,
            llm_latency_ms=200.0,
            praxeon_overhead_ms=4.0,
            execution_time_ms=0.0,
            total_step_latency_ms=204.0,
            physical_execution_attempted=True,
            physical_execution_allowed=False,
            physical_execution_success=None,
            is_error=False,
            cost_usd=0.0009,
        ),
    ]

    summary = EfficiencyCalculator.aggregate_session(
        session_id="sess_test_1",
        scale="short",
        model_name="deepseek-r1-7b",
        successful_completion=True,
        steps=steps,
    )

    assert summary.session_id == "sess_test_1"
    assert summary.total_steps == 2
    assert summary.successful_completion is True
    assert summary.total_tokens == 1250
    assert summary.total_tokens_in == 1100
    assert summary.total_tokens_out == 150
    assert summary.physical_executions_proposed == 2
    assert summary.physical_executions_allowed == 1
    assert summary.physical_executions_prevented == 1
    assert summary.execution_reduction_percentage == 50.0
    assert summary.total_praxeon_overhead_ms == 9.0
    assert summary.total_llm_latency_ms == 450.0


def test_comparative_metrics_crr_do_er_cpst():
    """Verifica el cálculo de las 4 métricas de eficiencia (Fase 1) comparando con baseline."""
    # Creamos 2 sesiones de PRAXEON
    praxeon_sessions = [
        SessionEfficiencySummary(
            session_id="prax_1",
            scale="medium",
            model_name="deepseek-r1-7b",
            total_steps=5,
            successful_completion=True,
            total_tokens_in=2000,
            total_tokens_out=400,
            total_tokens=2400,
            total_llm_calls=5,
            total_llm_latency_ms=1000.0,
            total_praxeon_overhead_ms=25.0,
            total_execution_time_ms=100.0,
            total_session_latency_ms=1125.0,
            decision_overhead_percentage=2.22,
            physical_executions_proposed=5,
            physical_executions_allowed=4,
            physical_executions_prevented=1,
            execution_reduction_percentage=20.0,
            errors_count=0,
            total_cost_usd=0.015,
        ),
        SessionEfficiencySummary(
            session_id="prax_2",
            scale="medium",
            model_name="deepseek-r1-7b",
            total_steps=6,
            successful_completion=True,
            total_tokens_in=2500,
            total_tokens_out=500,
            total_tokens=3000,
            total_llm_calls=6,
            total_llm_latency_ms=1200.0,
            total_praxeon_overhead_ms=30.0,
            total_execution_time_ms=120.0,
            total_session_latency_ms=1350.0,
            decision_overhead_percentage=2.22,
            physical_executions_proposed=6,
            physical_executions_allowed=5,
            physical_executions_prevented=1,
            execution_reduction_percentage=16.67,
            errors_count=0,
            total_cost_usd=0.018,
        ),
    ]

    # Baseline acumuló más tokens por loops y pasos ciegos
    baseline_sessions = [
        SessionEfficiencySummary(
            session_id="base_1",
            scale="medium",
            model_name="deepseek-r1-7b",
            total_steps=8,
            successful_completion=True,
            total_tokens_in=4000,
            total_tokens_out=800,
            total_tokens=4800,
            total_llm_calls=8,
            total_llm_latency_ms=1600.0,
            total_praxeon_overhead_ms=0.0,
            total_execution_time_ms=160.0,
            total_session_latency_ms=1760.0,
            decision_overhead_percentage=0.0,
            physical_executions_proposed=8,
            physical_executions_allowed=8,
            physical_executions_prevented=0,
            execution_reduction_percentage=0.0,
            errors_count=2,
            total_cost_usd=0.030,
        ),
        SessionEfficiencySummary(
            session_id="base_2",
            scale="medium",
            model_name="deepseek-r1-7b",
            total_steps=10,
            successful_completion=False,
            total_tokens_in=5000,
            total_tokens_out=1000,
            total_tokens=6000,
            total_llm_calls=10,
            total_llm_latency_ms=2000.0,
            total_praxeon_overhead_ms=0.0,
            total_execution_time_ms=200.0,
            total_session_latency_ms=2200.0,
            decision_overhead_percentage=0.0,
            physical_executions_proposed=10,
            physical_executions_allowed=10,
            physical_executions_prevented=0,
            execution_reduction_percentage=0.0,
            errors_count=3,
            total_cost_usd=0.038,
        ),
    ]

    metrics = EfficiencyCalculator.calculate_comparative_metrics(
        praxeon_sessions=praxeon_sessions,
        baseline_sessions=baseline_sessions,
    )

    assert metrics.total_tasks_evaluated == 2
    assert metrics.successful_tasks_count == 2
    assert metrics.task_success_rate == 1.0

    # 1. CRR: Total Praxeon = 5400 tokens vs Total Baseline = 10800 tokens -> Ahorro = 50.0%
    assert pytest.approx(metrics.context_reduction_ratio, rel=1e-2) == 50.0

    # 2. DO: Overhead total = 55.0 ms / 2475.0 ms total = ~2.22%
    assert pytest.approx(metrics.decision_overhead_percentage, rel=1e-2) == 2.22
    assert metrics.decision_overhead_ms > 0

    # 3. ER: Prevented = 2, Proposed = 11 -> 2 / 11 = ~18.18%
    assert pytest.approx(metrics.execution_reduction_rate, rel=1e-2) == 18.18

    # 4. CPST: Total cost = 0.033 / 2 exitosas = 0.0165 USD
    assert pytest.approx(metrics.cost_per_successful_task_usd, rel=1e-2) == 0.0165


def test_navigator_efficiency_telemetry():
    """Valida la captura reactiva de EfficiencyStepEvent y SessionEfficiencySummary en Navigator."""
    bus = EventBus()
    events = []
    bus.subscribe(lambda ev: events.append(ev) if isinstance(ev, EfficiencyStepEvent) else None)

    # Provider mockeado
    mock_provider = MagicMock()
    mock_provider.evaluate.return_value = [
        ProviderAssessment(
            provider="test-mock",
            available=True,
            confidence=0.95,
            loop_probability=0.0,
            grounded_probability=1.0,
            progress_probability=1.0,
        )
    ]
    mock_provider.model = "deepseek-r1-7b"

    # Executor seguro
    executor = SecureExecutor()
    nav = Navigator(
        provider=mock_provider,
        executor=executor,
        event_bus=bus,
        model_name="deepseek-r1-7b",
    )

    goal = Goal(objective="Test efficiency telemetry")
    nav.start_session(goal)

    action1 = ActionCandidate(
        id="act_1",
        description="Lectura de archivo README.md",
        rationale="Lectura de estado",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "README.md"}),
        metadata={"tokens_in": 150, "tokens_out": 40},
    )

    dec, obs = nav.step(action1)
    assert dec.status == DecisionStatus.ALLOW
    assert obs is not None
    assert len(nav.efficiency_records) == 1
    assert len(events) == 1

    ev = events[0]
    assert ev.step_index == 0
    assert ev.action_id == "act_1"
    assert ev.decision == "allow"
    assert ev.tokens_in == 150
    assert ev.tokens_out == 40
    assert ev.cost_usd > 0.0

    # Resumen de sesión
    summary = nav.get_efficiency_summary(scale="short", successful_completion=True)
    assert summary.total_steps == 1
    assert summary.total_tokens == 190
    assert summary.physical_executions_allowed == 1
    assert summary.physical_executions_prevented == 0


def test_benchmark_suite_generation():
    """Valida la generación de la suite canónica de benchmarks de eficiencia multiescala."""
    from praxeon.evaluation.efficiency_scenarios import BenchmarkScale, create_efficiency_benchmark_suite

    suite = create_efficiency_benchmark_suite(seed=42)
    assert len(suite) == 12

    short_scenarios = [s for s in suite if s.scale == BenchmarkScale.SHORT]
    med_scenarios = [s for s in suite if s.scale == BenchmarkScale.MEDIUM]
    long_scenarios = [s for s in suite if s.scale == BenchmarkScale.LONG]

    assert len(short_scenarios) == 4
    assert len(med_scenarios) == 4
    assert len(long_scenarios) == 4

    models = {s.model_name for s in suite}
    assert "deepseek-r1-7b" in models
    assert "llama-3-8b" in models
    assert "gpt-4o" in models
    assert "claude-3-5-sonnet" in models

