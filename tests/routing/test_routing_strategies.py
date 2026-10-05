"""Pruebas unitarias para las Estrategias Híbridas de Enrutamiento (Fase 7 - F7-03).

Valida:
- CostAwareRoutingStrategy: optimización de coste en modelos ligeros (Flash vs Pro).
- SemanticRoutingStrategy: asignación por máxima afinidad semántica con directivas y skills.
- AdaptiveRoutingStrategy: ponderación multicriterio (semántica, coste y capabilities).
- Invocación integrada a través de AgentRouter con conmutación dinámica de estrategias.
"""

import pytest

from praxeon.agents import AgentRegistry, AgentTemplateCatalog
from praxeon.domain.assessment import RiskLevel
from praxeon.routing import (
    AdaptiveRoutingStrategy,
    AgentRouter,
    CostAwareRoutingStrategy,
    RoutingStrategyType,
    SemanticRoutingStrategy,
    TaskComplexity,
    TaskRequirement,
)


@pytest.fixture
def populated_router():
    registry = AgentRegistry()
    registry.register(AgentTemplateCatalog.instantiate("developer", "ag_dev"))
    registry.register(AgentTemplateCatalog.instantiate("security_auditor", "ag_sec"))
    registry.register(AgentTemplateCatalog.instantiate("researcher", "ag_res"))
    registry.register(AgentTemplateCatalog.instantiate("writer", "ag_writer"))
    return AgentRouter(registry=registry)


def test_cost_aware_selects_flash_model_for_simple_task(populated_router):
    """Valida que para tareas de complejidad baja/media el ruteo por coste seleccione el modelo Flash más barato."""
    # Researcher utiliza gemini-1.5-flash mientras que Developer utiliza gemini-1.5-pro
    task = TaskRequirement(
        task_id="t_cost_01",
        prompt="Synthesize general documentation and summarize public references",
        complexity=TaskComplexity.LOW,
        required_tools=["view_file"],
        metadata={"classification": {"estimated_tokens": 2000}},
    )

    decision = populated_router.route(task, strategy=RoutingStrategyType.COST_AWARE)

    assert decision.strategy_used == RoutingStrategyType.COST_AWARE
    # Researcher (Flash) es ~10x más barato que Developer (Pro)
    assert decision.selected_agent_id == "ag_res"
    assert decision.estimated_cost < 0.001
    assert "Optimización de coste" in decision.rationale


def test_cost_aware_prioritizes_pro_model_for_high_complexity(populated_router):
    """Valida que para tareas de complejidad HIGH o CRITICAL se preserve la calidad eligiendo modelos Pro."""
    task = TaskRequirement(
        task_id="t_cost_high_01",
        prompt="Refactor critical distributed transactions and verify integrity invariants",
        complexity=TaskComplexity.HIGH,
        required_tools=["view_file"],
        metadata={"classification": {"estimated_tokens": 4000}},
    )

    decision = populated_router.route(task, strategy=RoutingStrategyType.COST_AWARE)

    assert decision.strategy_used == RoutingStrategyType.COST_AWARE
    # El modelo Developer (Pro) debe ser seleccionado sobre el Flash por la penalización de calidad
    assert decision.selected_agent_id == "ag_dev"


def test_semantic_routing_affinity(populated_router):
    """Valida que el ruteo semántico asigne al especialista técnico con mayor afinidad de vocabulario y skills."""
    # 1. Tarea de documentación técnica -> Writer
    task_writer = TaskRequirement(
        task_id="t_sem_writer",
        prompt="Draft comprehensive release notes and user guide documentation for the REST API",
    )
    dec_writer = populated_router.route(task_writer, strategy=RoutingStrategyType.SEMANTIC)
    assert dec_writer.selected_agent_id == "ag_writer"
    assert dec_writer.strategy_used == RoutingStrategyType.SEMANTIC
    assert dec_writer.confidence >= 0.65

    # 2. Tarea de investigación documental -> Researcher
    task_res = TaskRequirement(
        task_id="t_sem_res",
        prompt="Search arXiv literature and collect academic benchmarks on reasoning loops",
    )
    dec_res = populated_router.route(task_res, strategy=RoutingStrategyType.SEMANTIC)
    assert dec_res.selected_agent_id == "ag_res"
    assert dec_res.confidence >= 0.65


def test_adaptive_routing_multicriteria_balance(populated_router):
    """Valida que el ruteo adaptativo calcule armónicamente la utilidad combinada y provea telemetría."""
    task = TaskRequirement(
        task_id="t_adapt_01",
        prompt="Investigate literature on security vulnerabilities and write a summary report",
        complexity=TaskComplexity.MEDIUM,
        required_tools=["view_file"],
        metadata={"classification": {"estimated_tokens": 1500}},
    )

    decision = populated_router.route(task, strategy=RoutingStrategyType.ADAPTIVE)

    assert decision.strategy_used == RoutingStrategyType.ADAPTIVE
    assert "adaptive_breakdown" in decision.metadata
    breakdown = decision.metadata["adaptive_breakdown"]
    assert "utility" in breakdown
    assert "semantic_normalized" in breakdown
    assert "cost_normalized" in breakdown
    assert "capability_ratio" in breakdown
    assert 0.0 <= breakdown["utility"] <= 1.0


def test_custom_adaptive_weights_instantiation():
    """Valida la parametrización de pesos personalizados en AdaptiveRoutingStrategy."""
    strat = AdaptiveRoutingStrategy(
        weight_semantic=0.8,
        weight_cost=0.1,
        weight_capabilities=0.1,
    )
    assert round(strat.w_sem, 2) == 0.8
    assert round(strat.w_cost, 2) == 0.1
    assert round(strat.w_cap, 2) == 0.1
