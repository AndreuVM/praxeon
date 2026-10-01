"""Pruebas unitarias para la integración con TypeSafe AI (Modelo Jev)."""

from unittest.mock import MagicMock, patch
import pytest

from praxeon.config import PraxeonConfig
from praxeon.core.jev_engine import JEVEngine
from praxeon.core.state_graph import StateGraph
from praxeon.core.typesafe_client import TypeSafeJEVClient
from praxeon.models.schema import ActionCandidate, Step, StepType, Trajectory


def test_typesafe_client_unavailable_without_key():
    """Verifica que el cliente no está disponible si no hay API key."""
    cfg = PraxeonConfig(typesafe_api_key=None, use_typesafe_api=False)
    client = TypeSafeJEVClient(cfg)
    assert client.is_available() is False
    assert client.evaluate_candidate("meta", [], ActionCandidate(id="c1", description="desc")) is None


def test_typesafe_client_mock_evaluation():
    """Verifica que TypeSafeJEVClient procesa correctamente las respuestas tipadas de Jev."""
    cfg = PraxeonConfig(typesafe_api_key="test_dummy_key", use_typesafe_api=True)
    client = TypeSafeJEVClient(cfg)

    mock_noul = MagicMock()
    mock_noul.noul = True  # Loop detectado

    mock_choice = MagicMock()
    mock_choice.choice = "one_hop_tool_repeat"
    mock_choice.confidence = 0.95

    mock_score_progress = MagicMock()
    mock_score_progress.score = "counterproductive"

    mock_score_novelty = MagicMock()
    mock_score_novelty.score = "redundant"

    mock_response = MagicMock()
    mock_response.model = "jev"
    mock_response.nouls = {"is_loop": mock_noul}
    mock_response.choices = {"loop_type": mock_choice}
    mock_response.scores = {
        "progress": mock_score_progress,
        "novelty": mock_score_novelty,
    }

    client._client = MagicMock()
    client._client.system_one.return_value = mock_response

    candidate = ActionCandidate(
        id="cand_repeat",
        description="Reintentar comando flake8",
        tool_name="run_command",
        tool_args={"cmd": "flake8"},
    )
    result = client.evaluate_candidate(
        goal="Corregir sintaxis",
        history=[Step(id="s0", step_type=StepType.TOOL_CALL, tool_name="run_command")],
        candidate=candidate,
    )

    assert result is not None
    assert result["source"] == "typesafe_ai_jev"
    assert result["is_loop"] is True
    assert result["loop_penalty"] == 1.5
    assert result["p_progress"] == 0.05
    assert result["delta_u"] == 0.0


def test_jev_engine_integration_with_typesafe():
    """Verifica que JEVEngine use los resultados de TypeSafe AI cuando está habilitado."""
    cfg = PraxeonConfig(typesafe_api_key="dummy_key", use_typesafe_api=True)
    graph = StateGraph(cfg)
    graph.load_trajectory(Trajectory(session_id="ts_test", goal="Optimizar", steps=[]))

    engine = JEVEngine(graph, cfg)

    # Mock del cliente de TypeSafe dentro del motor
    engine.typesafe_client = MagicMock()
    engine.typesafe_client.is_available.return_value = True
    engine.typesafe_client.evaluate_candidate.return_value = {
        "source": "typesafe_ai_jev",
        "is_loop": False,
        "loop_type": None,
        "p_progress": 0.90,
        "delta_u": 0.85,
        "loop_penalty": 0.0,
        "confidence": 0.92,
        "model": "jev",
    }

    cand = ActionCandidate(id="cand_clean", description="Implementar cache LRU")
    score = engine.evaluate_candidate(cand)

    assert score.p_progress == 0.90
    assert score.delta_u == 0.85
    assert score.total_jev > 0.5
    assert score.details["typesafe_eval"] is not None


def test_typesafe_evaluate_step_chunk_clean():
    """Verifica que evaluate_step_chunk valide un bloque de pasos en una sola llamada a TypeSafe."""
    cfg = PraxeonConfig(typesafe_api_key="test_dummy_key", use_typesafe_api=True)
    client = TypeSafeJEVClient(cfg)

    mock_noul = MagicMock()
    mock_noul.noul = 0.05  # Seguro, no loop ni alucinación

    mock_div_choice = MagicMock()
    mock_div_choice.choice = "none"
    mock_div_choice.confidence = 0.98

    mock_halluc_choice = MagicMock()
    mock_halluc_choice.choice = "none"

    mock_prog = MagicMock()
    mock_prog.score = "significant_progress"

    mock_nov = MagicMock()
    mock_nov.score = "novel_action"

    mock_response = MagicMock()
    mock_response.model = "jev-latest"
    mock_response.nouls = {"has_hallucination_or_loop": mock_noul}
    mock_response.choices = {
        "divergence_step": mock_div_choice,
        "hallucination_type": mock_halluc_choice,
    }
    mock_response.scores = {
        "progress": mock_prog,
        "novelty": mock_nov,
    }

    client._client = MagicMock()
    client._client.system_one.return_value = mock_response

    candidates = [
        ActionCandidate(id="c0", description="Leer archivo", tool_name="read_file", tool_args={"path": "main.py"}),
        ActionCandidate(id="c1", description="Compilar proyecto", tool_name="run_command", tool_args={"cmd": "pytest"}),
    ]

    res = client.evaluate_step_chunk(goal="Verificar tests", history=[], candidates=candidates)
    assert res is not None
    assert res["all_safe"] is True
    assert res["is_loop"] is False
    assert res["is_hallucination"] is False
    assert res["p_progress"] == 0.80
    assert res["delta_u"] == 0.90
    assert res["total_candidates"] == 2
    # Comprobar que system_one se invocó exactamente UNA sola vez para todo el bloque
    assert client._client.system_one.call_count == 1


def test_typesafe_evaluate_step_chunk_detects_hallucination():
    """Verifica que evaluate_step_chunk detecte alucinaciones y apunte al paso infractor."""
    cfg = PraxeonConfig(typesafe_api_key="test_dummy_key", use_typesafe_api=True)
    client = TypeSafeJEVClient(cfg)

    mock_noul = MagicMock()
    mock_noul.noul = 0.95  # Alucinación detectada

    mock_div_choice = MagicMock()
    mock_div_choice.choice = "step_1"  # Paso en índice 1 alucina
    mock_div_choice.confidence = 0.94

    mock_halluc_choice = MagicMock()
    mock_halluc_choice.choice = "invented_fact_or_file"

    mock_prog = MagicMock()
    mock_prog.score = "counterproductive"

    mock_nov = MagicMock()
    mock_nov.score = "redundant"

    mock_response = MagicMock()
    mock_response.model = "jev-latest"
    mock_response.nouls = {"has_hallucination_or_loop": mock_noul}
    mock_response.choices = {
        "divergence_step": mock_div_choice,
        "hallucination_type": mock_halluc_choice,
    }
    mock_response.scores = {
        "progress": mock_prog,
        "novelty": mock_nov,
    }

    client._client = MagicMock()
    client._client.system_one.return_value = mock_response

    candidates = [
        ActionCandidate(id="c0", description="Paso válido", tool_name="read_file", tool_args={"path": "main.py"}),
        ActionCandidate(id="c1", description="Alucinación de archivo inexistente", tool_name="read_file", tool_args={"path": "fake_nonexistent.py"}),
    ]

    res = client.evaluate_step_chunk(goal="Verificar tests", history=[], candidates=candidates)
    assert res is not None
    assert res["all_safe"] is False
    assert res["is_hallucination"] is True
    assert res["hallucination_type"] == "invented_fact_or_file"
    assert res["flagged_index"] == 1
    assert res["loop_penalty"] > 1.0
    assert client._client.system_one.call_count == 1

