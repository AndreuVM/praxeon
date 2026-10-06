"""Pruebas unitarias para Task 28 (Fase 3 - P1-ROUTING).

Valida:
1. Configuración de branching adaptativo como experimental y desactivado por defecto.
2. Mantenimiento del baseline determinista (RULE_BASED en ruteo, GREEDY k=1 en búsqueda).
3. Gobernanza y fallback determinista seguro ante invocaciones de branching no autorizadas.
4. Etiquetado estructurado y auditable de decisiones como 'experimental'.
5. Integración con PraxeonConfig (propiedades, migraciones y variables de entorno).
"""

import pytest

from praxeon.agents import AgentRegistry, AgentTemplateCatalog
from praxeon.config import AdaptiveBranchingConfig, PraxeonConfig
from praxeon.domain import ActionCandidate, BranchPath, BranchStatus, Goal, ToolCall
from praxeon.reasoning import (
    BranchPruner,
    SearchConfig,
    SearchResult,
    SearchStrategy,
    TreeSearchEngine,
)
from praxeon.routing import (
    AdaptiveRoutingStrategy,
    AgentRouter,
    RoutingStrategyType,
    TaskComplexity,
    TaskRequirement,
)
from praxeon.runtime.state import SessionState


@pytest.fixture
def populated_registry():
    registry = AgentRegistry()
    registry.register(AgentTemplateCatalog.instantiate("developer", "ag_dev"))
    registry.register(AgentTemplateCatalog.instantiate("security_auditor", "ag_sec"))
    registry.register(AgentTemplateCatalog.instantiate("researcher", "ag_res"))
    registry.register(AgentTemplateCatalog.instantiate("writer", "ag_writer"))
    return registry


@pytest.fixture
def sample_task():
    return TaskRequirement(
        task_id="t_branching_01",
        prompt="Auditar vulnerabilidades de dependencias y redactar reporte técnico",
        complexity=TaskComplexity.MEDIUM,
        required_tools=["view_file"],
        metadata={"classification": {"estimated_tokens": 1200}},
    )


def test_default_routing_baseline_is_deterministic(populated_registry, sample_task):
    """Valida que el baseline de enrutador sea estrictamente determinista (RULE_BASED) por defecto."""
    router = AgentRouter(registry=populated_registry)
    assert router.default_strategy == RoutingStrategyType.RULE_BASED

    decision = router.route(sample_task)
    assert decision.strategy_used == RoutingStrategyType.RULE_BASED
    assert decision.metadata.get("experimental") is False
    assert decision.metadata.get("is_experimental") is False
    assert decision.metadata.get("baseline_mode") == "deterministic"


def test_adaptive_routing_marked_as_experimental(populated_registry, sample_task):
    """Valida que las decisiones adaptativas incluyan telemetría explícita de experimento."""
    router = AgentRouter(registry=populated_registry)
    decision = router.route(sample_task, strategy=RoutingStrategyType.ADAPTIVE)

    assert decision.strategy_used == RoutingStrategyType.ADAPTIVE
    assert decision.metadata.get("experimental") is True
    assert decision.metadata.get("is_experimental") is True
    assert decision.metadata.get("baseline_mode") == "adaptive_experimental"
    assert "[EXPERIMENTAL]" in decision.rationale


def test_enforced_deterministic_baseline_falls_back_from_adaptive(populated_registry, sample_task):
    """Valida que con gobernanza estricta, invocar ADAPTIVE sin permiso realice fallback a RULE_BASED."""
    router = AgentRouter(
        registry=populated_registry,
        allow_experimental_branching=False,
        enforce_deterministic_baseline=True,
    )

    decision = router.route(sample_task, strategy=RoutingStrategyType.ADAPTIVE)

    # Debe haber caído de forma segura al baseline determinista
    assert decision.strategy_used == RoutingStrategyType.RULE_BASED
    assert decision.metadata.get("fallback_to_deterministic") is True
    assert decision.metadata.get("experimental_branching_blocked") is True
    assert decision.metadata.get("experimental") is False
    assert "[BASELINE DETERMINISTA]" in decision.rationale


def test_explicit_allow_experimental_branching_permits_adaptive(populated_registry, sample_task):
    """Valida que habilitar allow_experimental_branching permita la ejecución adaptativa experimental."""
    # Permiso a nivel de router
    router = AgentRouter(
        registry=populated_registry,
        allow_experimental_branching=True,
        enforce_deterministic_baseline=True,
    )
    decision = router.route(sample_task, strategy=RoutingStrategyType.ADAPTIVE)
    assert decision.strategy_used == RoutingStrategyType.ADAPTIVE
    assert decision.metadata.get("experimental") is True

    # Permiso a nivel de metadata de tarea
    strict_router = AgentRouter(
        registry=populated_registry,
        allow_experimental_branching=False,
        enforce_deterministic_baseline=True,
    )
    task_opt_in = TaskRequirement(
        task_id="t_branching_optin",
        prompt="Auditar vulnerabilidades de dependencias y redactar reporte técnico",
        complexity=TaskComplexity.MEDIUM,
        required_tools=["view_file"],
        metadata={"allow_experimental_branching": True},
    )
    opt_in_decision = strict_router.route(task_opt_in, strategy=RoutingStrategyType.ADAPTIVE)
    assert opt_in_decision.strategy_used == RoutingStrategyType.ADAPTIVE
    assert opt_in_decision.metadata.get("experimental") is True


def test_praxeon_config_adaptive_branching_parameters():
    """Valida la estructura Pydantic de AdaptiveBranchingConfig en PraxeonConfig."""
    cfg = PraxeonConfig()
    assert isinstance(cfg.adaptive, AdaptiveBranchingConfig)
    assert cfg.adaptive.enabled is False
    assert cfg.adaptive.strategy == "deterministic"
    assert cfg.adaptive.max_branching_factor == 3
    assert cfg.adaptive.fallback_to_deterministic is True
    assert cfg.branching is cfg.adaptive

    # Compatibilidad con alias 'branching' en constructor
    cfg_custom = PraxeonConfig(branching={"enabled": True, "strategy": "adaptive", "max_branching_factor": 5})
    assert cfg_custom.adaptive.enabled is True
    assert cfg_custom.adaptive.strategy == "adaptive"
    assert cfg_custom.adaptive.max_branching_factor == 5


def test_tree_search_engine_defaults_to_deterministic_greedy_k1():
    """Valida que SearchConfig y TreeSearchEngine tengan como baseline GREEDY (k=1)."""
    cfg_default = SearchConfig()
    assert cfg_default.branching_factor == 1
    assert cfg_default.beam_width == 1
    assert cfg_default.strategy == SearchStrategy.GREEDY
    assert cfg_default.is_experimental is False

    goal = Goal(objective="Diagnosticar logs", success_criteria=["ok"])
    session = SessionState(session_id="sess_det_search", goal=goal)

    engine = TreeSearchEngine(config=cfg_default)

    def simple_candidates(branch: BranchPath, state: SessionState, k: int):
        return [
            ActionCandidate(
                id=f"act_{branch.depth + 1}",
                description="Paso determinista lineal",
                tool_call=ToolCall(tool_name="read_file", arguments={"path": "main.py"}),
                metadata={"confidence": 0.95},
            )
        ]

    result = engine.run_search(session, simple_candidates)
    assert result.success is True
    assert result.is_experimental is False
    assert result.strategy == SearchStrategy.GREEDY
    assert result.branching_factor == 1


def test_tree_search_engine_multibranch_flags_as_experimental():
    """Valida que cuando se activa branching multirruta (k>1 / BEAM_SEARCH), se marque como experimental."""
    cfg_beam = SearchConfig(strategy=SearchStrategy.BEAM_SEARCH, branching_factor=3, beam_width=2, max_depth=2)
    assert cfg_beam.strategy == SearchStrategy.BEAM_SEARCH
    assert cfg_beam.branching_factor == 3

    goal = Goal(objective="Búsqueda exploratoria", success_criteria=["ok"])
    session = SessionState(session_id="sess_exp_search", goal=goal)

    engine = TreeSearchEngine(config=cfg_beam)

    def branching_candidates(branch: BranchPath, state: SessionState, k: int):
        return [
            ActionCandidate(
                id=f"act_opt_{i}_{branch.depth + 1}",
                description=f"Rama exploratoria {i}",
                tool_call=ToolCall(tool_name="read_file", arguments={"path": f"src/{i}.py"}),
                metadata={"confidence": 0.8 + (i * 0.05)},
            )
            for i in range(k)
        ]

    result = engine.run_search(session, branching_candidates)
    assert result.success is True
    assert result.is_experimental is True
    assert result.explored_branches_count > 1
    data = result.to_dict()
    assert data["is_experimental"] is True
