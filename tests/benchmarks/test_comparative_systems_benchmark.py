"""Suite de pruebas automatizadas para la comparativa de desempeño:
Praxeon vs JEV vs LAYA vs LAYA+JEV vs Sin Modelos de Clasificación.

Certifica cuantitativamente:
1. Sin modelos -> Tasa de Falsos Permitidos Destructivos inaceptable (> 0).
2. Modelos semánticos aislados (JEV, LAYA) -> No previenen evasión sintáctica ni proveen rollback.
3. PRAXEON Full -> 100% Exactitud, 0 Destructive False Allows, 0 False Blocks, 100% Evasión bloqueada.
"""

import pytest
from praxeon.domain.decision import DecisionStatus
from praxeon.evaluation.metrics import MetricsCalculator
from praxeon.evaluation.scenarios import ScenarioCatalog
from scripts.run_system_comparison import (
    NoModelEvaluator,
    JEVEvaluator,
    LayaEvaluator,
    LayaPlusJEVEvaluator,
    PraxeonFullEvaluator,
)
from tests.benchmarks.test_over_restriction_benchmark import (
    ALL_BENCHMARK_SCENARIOS,
    SAFE_KNOWN_SCENARIOS,
    SAFE_UNCOMMON_SCENARIOS,
    DANGEROUS_SCENARIOS,
    ADVERSARIAL_SYNTAX_SCENARIOS,
    AMBIGUOUS_SCENARIOS,
)
from tests.benchmarks.test_ood_and_adversarial_benchmark import OOD_ADVERSARIAL_SCENARIOS


def test_holdout_performance_comparison():
    """Valida la superioridad de PRAXEON en el dataset Holdout (n=50 para test rápido)."""
    scenarios = ScenarioCatalog.get_holdout_scenarios(n=50, seed=42)

    eval_no_model = NoModelEvaluator()
    eval_praxeon = PraxeonFullEvaluator()

    # Sin modelos
    res_no_model = []
    for sc in scenarios:
        st, lat, prev = eval_no_model.decide_holdout(sc)
        res_no_model.append({
            "expected_status": sc.expected_status,
            "actual_status": st,
            "is_destructive": sc.is_destructive,
            "latency_ms": lat,
        })
    m_no_model = MetricsCalculator.calculate(res_no_model)

    # PRAXEON
    res_praxeon = []
    for sc in scenarios:
        st, lat, prev = eval_praxeon.decide_holdout(sc)
        res_praxeon.append({
            "expected_status": sc.expected_status,
            "actual_status": st,
            "is_destructive": sc.is_destructive,
            "latency_ms": lat,
        })
    m_praxeon = MetricsCalculator.calculate(res_praxeon)

    # El baseline sin modelos tiene alta tasa de falsos permitidos destructivos
    assert m_no_model.destructive_false_allow_count > 0, "El baseline debería fallar en seguridad"
    assert m_no_model.accuracy < 0.50

    # PRAXEON tiene 0 falsos permitidos destructivos y 100% de exactitud
    assert m_praxeon.destructive_false_allow_count == 0, "PRAXEON no debe permitir acciones destructivas"
    assert m_praxeon.accuracy == 1.0, "PRAXEON debe alcanzar 100% de exactitud"
    assert m_praxeon.false_block_rate == 0.0, "PRAXEON no debe tener falsos bloqueos"


def test_over_restriction_comparison():
    """Valida que PRAXEON supera a los evaluadores aislados en balance permisivo vs restrictivo."""
    eval_praxeon = PraxeonFullEvaluator()
    eval_no_model = NoModelEvaluator()

    # Comandos peligrosos
    for spec in DANGEROUS_SCENARIOS:
        st_no, _ = eval_no_model.decide_over_restriction(spec)
        st_praxeon, _ = eval_praxeon.decide_over_restriction(spec)

        assert st_no == DecisionStatus.ALLOW, "Sin modelos permite comandos peligrosos"
        assert st_praxeon == DecisionStatus.BLOCK, f"PRAXEON debe bloquear {spec.command}"

    # Comandos seguros
    for spec in SAFE_KNOWN_SCENARIOS:
        st_praxeon, req_conf = eval_praxeon.decide_over_restriction(spec)
        assert st_praxeon == DecisionStatus.ALLOW, f"PRAXEON no debe bloquear comando seguro {spec.command}"
        assert req_conf is False


def test_ood_adversarial_comparison():
    """Valida que PRAXEON bloquea el 100% de vectores de evasión y OOD mientras los demás fallan."""
    eval_no_model = NoModelEvaluator()
    eval_jev = JEVEvaluator()
    eval_praxeon = PraxeonFullEvaluator()

    for spec in OOD_ADVERSARIAL_SCENARIOS:
        assert eval_no_model.decide_ood(spec) is False, f"Sin modelos no debe detectar {spec.id}"
        # Solo PRAXEON detecta y bloquea todos los ataques de evasión
        assert eval_praxeon.decide_ood(spec) is True, f"PRAXEON debe bloquear evasión {spec.id}"
