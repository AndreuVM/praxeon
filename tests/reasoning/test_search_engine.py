"""Pruebas unitarias para el motor de exploración con branching factor k (Fase 3 - F3-02).

Valida:
- Modo lineal Greedy (k=1)
- Beam Search con branching factor k=3 y k=5
- Integración con poda temprana (BranchPruner descarta ramas peligrosas)
- Cálculo adaptativo de k según riesgo e incertidumbre
- Consolidación formal de la rama ganadora (COMMITTED)
"""

import pytest
from typing import List

from praxeon.domain import (
    ActionCandidate,
    BranchPath,
    BranchStatus,
    Goal,
    ToolCall,
)
from praxeon.reasoning import (
    BranchPruner,
    SearchConfig,
    SearchResult,
    SearchStrategy,
    TreeSearchEngine,
)
from praxeon.runtime.state import SessionState


@pytest.fixture
def mock_session():
    goal = Goal(
        objective="Investigar logs, corregir fallo de memoria en auth.py y validar tests",
        success_criteria=["auth.py reparado", "tests pasando"],
    )
    return SessionState(session_id="sess_search_test", goal=goal)


def test_search_greedy_linear(mock_session):
    """Valida la estrategia Greedy (k=1): avanza estrictamente de forma lineal sin bifurcaciones."""
    engine = TreeSearchEngine(config=SearchConfig(strategy=SearchStrategy.GREEDY, max_depth=3))

    def candidate_gen(branch: BranchPath, state: SessionState, k: int) -> List[ActionCandidate]:
        step_num = branch.depth + 1
        return [
            ActionCandidate(
                id=f"act_step_{step_num}",
                description=f"Acción unilineal en paso {step_num}",
                tool_call=ToolCall(tool_name="read_file", arguments={"path": f"src/auth_{step_num}.py"}),
                metadata={"confidence": 0.9},
            )
        ]

    result = engine.run_search(mock_session, candidate_gen)

    assert result.success is True
    assert result.committed_branch is not None
    assert result.committed_branch.status == BranchStatus.COMMITTED
    assert result.committed_branch.depth == 3
    # En greedy con 1 candidato por paso y profundidad 3, debe haber exactamente 1 rama activa consolidada
    assert result.explored_branches_count == 4  # root + 3 pasos lineales
    assert result.pruned_branches_count == 0


def test_search_beam_search_branching_k3(mock_session):
    """Valida Beam Search con k=3: genera 3 alternativas por bifurcación y selecciona la de mayor score."""
    cfg = SearchConfig(strategy=SearchStrategy.BEAM_SEARCH, branching_factor=3, beam_width=2, max_depth=2)
    engine = TreeSearchEngine(config=cfg)

    def candidate_gen(branch: BranchPath, state: SessionState, k: int) -> List[ActionCandidate]:
        step_num = branch.depth + 1
        candidates = []
        for i in range(k):
            # Alternativa 0 tiene mayor confianza y alineamiento
            conf = 0.95 if i == 0 else 0.60 - (i * 0.1)
            candidates.append(
                ActionCandidate(
                    id=f"act_d{step_num}_alt{i}",
                    description=f"Inspeccionar auth.py para corregir fallo opción {i}",
                    tool_call=ToolCall(tool_name="read_file", arguments={"path": f"src/auth_opt_{i}.py"}),
                    metadata={"confidence": conf},
                )
            )
        return candidates

    result = engine.run_search(mock_session, candidate_gen)

    assert result.success is True
    assert result.committed_branch is not None
    assert result.committed_branch.status == BranchStatus.COMMITTED
    assert result.committed_branch.depth == 2
    # El score del branch ganador debe ser alto
    assert result.committed_branch.score.composite_score > 0.6
    # Hubo ramas abandonadas o filtradas por beam_width
    assert result.abandoned_branches_count > 0


def test_search_pruning_integration(mock_session):
    """Valida que si una rama intenta una acción peligrosa (rm -rf), el pruner la descarte y el engine elija la segura."""
    pruner = BranchPruner()
    engine = TreeSearchEngine(pruner=pruner, config=SearchConfig(strategy=SearchStrategy.BEAM_SEARCH, branching_factor=2, max_depth=1))

    def candidate_gen(branch: BranchPath, state: SessionState, k: int) -> List[ActionCandidate]:
        return [
            # Alternativa destructiva (debe ser podada por seguridad)
            ActionCandidate(
                id="act_destructive",
                description="Borrar todo para reiniciar entorno",
                tool_call=ToolCall(tool_name="run_command", arguments={"command": "rm -rf /"}),
                metadata={"confidence": 0.99},
            ),
            # Alternativa segura (debe sobrevivir y ser la committed)
            ActionCandidate(
                id="act_safe",
                description="Consultar git diff para auth.py",
                tool_call=ToolCall(tool_name="run_command", arguments={"command": "git diff"}),
                metadata={"confidence": 0.85},
            ),
        ]

    result = engine.run_search(mock_session, candidate_gen)

    assert result.success is True
    assert result.pruned_branches_count >= 1
    # La rama ganadora debe contener la acción segura
    assert result.committed_branch is not None
    assert len(result.committed_branch.steps) == 1
    assert result.committed_branch.steps[0].action.id == "act_safe"
    # La rama podada quedó registrada en metadata de la sesión
    assert len(mock_session.metadata.get("pruned_branches", [])) >= 1


def test_search_adaptive_k_logic(mock_session):
    """Valida la función determine_adaptive_k según nivel de confianza y riesgo."""
    engine = TreeSearchEngine()

    # Confianza muy alta (0.92) y bajo riesgo -> k=1
    k_easy = engine.determine_adaptive_k(confidence=0.92, risk_level="low")
    assert k_easy == 1

    # Confianza baja (0.50) o riesgo medio -> k se incrementa (3 a 5)
    k_hard = engine.determine_adaptive_k(confidence=0.50, risk_level="low")
    assert k_hard >= 3

    # Riesgo crítico -> k sube
    k_risky = engine.determine_adaptive_k(confidence=0.85, risk_level="high")
    assert k_risky >= 3
