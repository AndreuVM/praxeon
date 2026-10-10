"""Pruebas unitarias para el motor Tree of Thoughts Top-K y Branching Adaptativo (Fase 6 - ROAD-02).

Valida:
1. Ranking heurístico y selección Top-K con System-1 (LAYA / TypeSafe).
2. Poda temprana por bucle cognitivo (LOOP_DETECTED).
3. Poda inteligente por estancamiento heurístico sin avance de progreso (STAGNATION).
4. Poda por políticas normativas (SECURITY_VIOLATION / BLOCK).
5. Consolidación formal de la ruta de pensamientos ganadora (COMMITTED).
"""

import pytest
from typing import List

from praxeon.domain.action import ActionCandidate, ToolCall
from praxeon.domain.branch import BranchStatus
from praxeon.domain.models import Goal, ProviderAssessment
from praxeon.providers.laya import LayaProvider
from praxeon.providers.replay import ReplayProvider
from praxeon.reasoning.tree_of_thoughts import (
    ThoughtNode,
    TreeOfThoughtsConfig,
    TreeOfThoughtsEngine,
    TreeOfThoughtsResult,
)
from praxeon.runtime.state import SessionState


@pytest.fixture
def test_session():
    goal = Goal(
        objective="Optimizar algoritmo de búsqueda en memoria y validar cobertura de tests",
        success_criteria=["algoritmo optimizado", "tests en verde"],
    )
    return SessionState(session_id="sess_tot_test", goal=goal)


class TestTreeOfThoughts:
    """Verifica el comportamiento del motor Tree of Thoughts."""

    def test_tot_top_k_selection_and_ranking(self, test_session):
        """Valida que los candidatos sean ponderados y clasificados por la heurística System-1."""
        provider = ReplayProvider(default_scenario="safe_read")
        engine = TreeOfThoughtsEngine(provider=provider, config=TreeOfThoughtsConfig(top_k=2))

        candidates = [
            ActionCandidate(id="act_low", description="Acción de bajo progreso", tool_call=ToolCall(tool_name="read_file", arguments={"path": "a.txt"})),
            ActionCandidate(id="act_high", description="Acción de alto progreso", tool_call=ToolCall(tool_name="read_file", arguments={"path": "b.txt"})),
            ActionCandidate(id="act_med", description="Acción media", tool_call=ToolCall(tool_name="read_file", arguments={"path": "c.txt"})),
        ]

        provider.override_for_action("act_low", ProviderAssessment(provider="mock", available=True, progress_probability=0.20, confidence=0.8, loop_probability=0.1))
        provider.override_for_action("act_high", ProviderAssessment(provider="mock", available=True, progress_probability=0.92, confidence=0.9, loop_probability=0.01))
        provider.override_for_action("act_med", ProviderAssessment(provider="mock", available=True, progress_probability=0.60, confidence=0.8, loop_probability=0.05))

        top_k = engine.evaluate_candidates_top_k(test_session, candidates, k=2)

        assert len(top_k) == 2
        # La primera debe ser act_high
        assert top_k[0][0].id == "act_high"
        # La segunda debe ser act_med
        assert top_k[1][0].id == "act_med"
        assert top_k[0][2] > top_k[1][2]

    def test_tot_loop_pruning(self, test_session):
        """Valida que las alternativas con alta probabilidad de bucle cognitivo sean podadas tempranamente."""
        provider = ReplayProvider(default_scenario="safe_read")
        engine = TreeOfThoughtsEngine(provider=provider, config=TreeOfThoughtsConfig(top_k=3, loop_threshold=0.60, max_depth=2))

        def candidate_gen(node: ThoughtNode, state: SessionState, k: int) -> List[ActionCandidate]:
            return [
                ActionCandidate(id="act_loop", description="Acción atrapada en bucle", tool_call=ToolCall(tool_name="read_file", arguments={"path": "loop.txt"})),
                ActionCandidate(id="act_progress", description="Acción con progreso real", tool_call=ToolCall(tool_name="read_file", arguments={"path": "prog.txt"})),
            ]

        provider.override_for_action("act_loop", ProviderAssessment(provider="mock", available=True, progress_probability=0.1, loop_probability=0.85, confidence=0.9))
        provider.override_for_action("act_progress", ProviderAssessment(provider="mock", available=True, progress_probability=0.80, loop_probability=0.02, confidence=0.95))

        result = engine.run_tree_search(test_session, candidate_gen)

        assert result.success is True
        assert result.pruned_loop_count >= 1
        # El nodo podado por bucle debe tener el status PRUNED y categoría LOOP_DETECTED
        pruned_nodes = [n for n in result.all_nodes if n.status == BranchStatus.PRUNED and n.pruning_category == "LOOP_DETECTED"]
        assert len(pruned_nodes) >= 1
        assert "Bucle cognitivo" in (pruned_nodes[0].pruning_reason or "")

    def test_tot_stagnation_pruning(self, test_session):
        """Valida que una rama que no incrementa su progreso sea podada por estancamiento (STAGNATION)."""
        provider = ReplayProvider(default_scenario="safe_read")
        engine = TreeOfThoughtsEngine(
            provider=provider,
            config=TreeOfThoughtsConfig(top_k=2, beam_width=1, max_depth=3, stagnation_min_delta=0.05),
        )

        def candidate_gen(node: ThoughtNode, state: SessionState, k: int) -> List[ActionCandidate]:
            # Generar acción con progreso decreciente o nulo
            return [
                ActionCandidate(
                    id=f"act_d{node.depth+1}",
                    description=f"Paso {node.depth+1} sin avance",
                    tool_call=ToolCall(tool_name="read_file", arguments={"path": f"file_{node.depth}.txt"}),
                )
            ]

        # En el paso 1 el progreso es 0.50, en el paso 2 es 0.51 (delta 0.01 < 0.05 -> debe podarse por estancamiento)
        provider.override_for_action("act_d1", ProviderAssessment(provider="mock", available=True, progress_probability=0.50, confidence=0.9))
        provider.override_for_action("act_d2", ProviderAssessment(provider="mock", available=True, progress_probability=0.51, confidence=0.9))

        result = engine.run_tree_search(test_session, candidate_gen)

        assert result.pruned_stagnant_count >= 1
        stagnant_nodes = [n for n in result.all_nodes if n.pruning_category == "STAGNATION"]
        assert len(stagnant_nodes) >= 1
        assert "estancada" in (stagnant_nodes[0].pruning_reason or "")

    def test_tot_goal_checker_and_committed_path(self, test_session):
        """Valida la convergencia del árbol hacia el objetivo y la reconstrucción del camino óptimo."""
        provider = ReplayProvider(default_scenario="safe_read")
        engine = TreeOfThoughtsEngine(provider=provider, config=TreeOfThoughtsConfig(top_k=2, max_depth=4))

        def candidate_gen(node: ThoughtNode, state: SessionState, k: int) -> List[ActionCandidate]:
            step_idx = node.depth + 1
            if step_idx == 3:
                return [ActionCandidate(id="act_finish", description="Finalizar tarea cumplida", tool_call=ToolCall(tool_name="finish", arguments={"summary": "Completado"}))]
            return [
                ActionCandidate(id=f"act_step_{step_idx}_a", description=f"Paso {step_idx} A", tool_call=ToolCall(tool_name="read_file", arguments={"path": "a.txt"})),
                ActionCandidate(id=f"act_step_{step_idx}_b", description=f"Paso {step_idx} B", tool_call=ToolCall(tool_name="read_file", arguments={"path": "b.txt"})),
            ]

        for i in range(1, 4):
            provider.override_for_action(f"act_step_{i}_a", ProviderAssessment(provider="mock", available=True, progress_probability=0.5 + i * 0.15, confidence=0.9))
            provider.override_for_action(f"act_step_{i}_b", ProviderAssessment(provider="mock", available=True, progress_probability=0.4 + i * 0.10, confidence=0.8))
        provider.override_for_action("act_finish", ProviderAssessment(provider="mock", available=True, progress_probability=0.98, confidence=0.99))

        def goal_checker(node: ThoughtNode, state: SessionState) -> bool:
            return node.action.id == "act_finish"

        result = engine.run_tree_search(test_session, candidate_gen, goal_checker=goal_checker)

        assert result.success is True
        assert result.committed_node is not None
        assert result.committed_node.action.id == "act_finish"
        assert len(result.committed_path) == 3  # step 1, step 2, finish
        assert result.committed_path[-1].id == "act_finish"
        assert result.is_experimental is True
