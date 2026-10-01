"""Pruebas unitarias para el motor de evaluación y ranking JEV con TypeSafe AI."""

from unittest.mock import MagicMock
import pytest

from praxeon.core.jev_engine import JEVEngine
from praxeon.core.state_graph import StateGraph
from praxeon.models.schema import ActionCandidate, Step, StepType, Trajectory


@pytest.fixture
def stuck_in_loop_graph() -> StateGraph:
    """Fixture que simula un agente con historial de compilación."""
    graph = StateGraph()
    traj = Trajectory(
        session_id="session_jev_test",
        goal="Corregir error de tipo en main.py y hacer que compile",
        steps=[
            Step(id="s0", step_type=StepType.THOUGHT, content="Revisar error de compilación"),
            Step(id="s1", step_type=StepType.TOOL_CALL, tool_name="bash", tool_args={"cmd": "mypy main.py"}),
            Step(id="s2", step_type=StepType.OBSERVATION, content="main.py:10: error: Incompatible types in assignment"),
        ],
    )
    graph.load_trajectory(traj)
    return graph


def test_repetitive_action_receives_negative_jev(stuck_in_loop_graph):
    """Verifica que una acción calificada como bucle por TypeSafe recibe penalización y JEV negativo."""
    engine = JEVEngine(stuck_in_loop_graph)
    engine.typesafe_client = MagicMock()
    engine.typesafe_client.is_available.return_value = True
    engine.typesafe_client.evaluate_candidate.return_value = {
        "source": "typesafe_ai_jev",
        "is_loop": True,
        "p_progress": 0.05,
        "delta_u": 0.0,
        "loop_penalty": 1.5,
    }

    repetitive_action = ActionCandidate(
        id="cand_repeat",
        description="Reintentar mypy por tercera vez",
        tool_name="bash",
        tool_args={"cmd": "mypy main.py"},
    )

    score = engine.evaluate_candidate(repetitive_action)

    assert score.loop_penalty >= 1.0
    assert score.delta_u == 0.0
    assert score.total_jev < 0.0


def test_novel_diagnostic_action_receives_positive_jev(stuck_in_loop_graph):
    """Verifica que una acción constructiva evaluada por TypeSafe recibe JEV positivo."""
    engine = JEVEngine(stuck_in_loop_graph)
    engine.typesafe_client = MagicMock()
    engine.typesafe_client.is_available.return_value = True
    engine.typesafe_client.evaluate_candidate.return_value = {
        "source": "typesafe_ai_jev",
        "is_loop": False,
        "p_progress": 0.85,
        "delta_u": 0.80,
        "loop_penalty": 0.0,
    }

    novel_action = ActionCandidate(
        id="cand_inspect",
        description="Leer main.py para inspeccionar el tipo",
        tool_name="read_file",
        tool_args={"path": "main.py"},
    )

    score = engine.evaluate_candidate(novel_action)

    assert score.delta_u > 0.5
    assert score.loop_penalty == 0.0
    assert score.total_jev > 0.5


def test_action_ranking_order(stuck_in_loop_graph):
    """Verifica que el ranking ordene correctamente las acciones evaluadas por TypeSafe."""
    engine = JEVEngine(stuck_in_loop_graph)
    engine.typesafe_client = MagicMock()
    engine.typesafe_client.is_available.return_value = True

    def mock_eval(goal, history, candidate):
        if candidate.id == "cand_repeat":
            return {"is_loop": True, "p_progress": 0.05, "delta_u": 0.0, "loop_penalty": 1.5}
        elif candidate.id == "cand_edit":
            return {"is_loop": False, "p_progress": 0.90, "delta_u": 0.80, "loop_penalty": 0.0}
        else:
            return {"is_loop": False, "p_progress": 0.60, "delta_u": 0.50, "loop_penalty": 0.0}

    engine.typesafe_client.evaluate_candidate.side_effect = mock_eval

    cand_repeat = ActionCandidate(id="cand_repeat", description="Reejecutar mypy")
    cand_inspect = ActionCandidate(id="cand_inspect", description="Inspeccionar")
    cand_edit = ActionCandidate(id="cand_edit", description="Corregir código")

    ranked = engine.rank_candidates([cand_repeat, cand_inspect, cand_edit])

    assert ranked[0][0].id == "cand_edit"
    assert ranked[-1][0].id == "cand_repeat"
    assert ranked[0][1].total_jev > ranked[-1][1].total_jev

