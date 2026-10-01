"""Pruebas unitarias para diagnóstico de trazas y trayectorias mediante TypeSafe AI."""

from unittest.mock import MagicMock
import pytest
from praxeon.config import PraxeonConfig
from praxeon.core.jev_engine import JEVEngine
from praxeon.core.state_graph import StateGraph
from praxeon.core.typesafe_client import TypeSafeJEVClient
from praxeon.models.schema import LoopReport, LoopType, Step, StepType, Trajectory


@pytest.fixture
def sample_trajectory() -> Trajectory:
    return Trajectory(
        session_id="diag_session",
        goal="Corregir error de compilación",
        steps=[
            Step(id="s1", step_type=StepType.THOUGHT, content="Analizar error"),
            Step(id="s2", step_type=StepType.TOOL_CALL, tool_name="cargo", tool_args={"subcmd": "check"}),
            Step(id="s3", step_type=StepType.TOOL_CALL, tool_name="cargo", tool_args={"subcmd": "check"}),
        ],
    )


def test_diagnose_trajectory_with_typesafe_detects_loop(sample_trajectory):
    """Verifica que diagnose_trajectory identifique bucles usando TypeSafe AI."""
    cfg = PraxeonConfig(typesafe_api_key="mock_key", use_typesafe_api=True)
    client = TypeSafeJEVClient(cfg)
    client._client = MagicMock()

    mock_noul = MagicMock()
    mock_noul.noul = True

    mock_choice = MagicMock()
    mock_choice.choice = "one_hop_tool_repeat"
    mock_choice.confidence = 0.95

    mock_score = MagicMock()
    mock_score.score = 3

    mock_resp = MagicMock()
    mock_resp.nouls = {"has_loop_or_flaw": mock_noul}
    mock_resp.choices = {"loop_type": mock_choice}
    mock_resp.scores = {"severity": mock_score}

    client._client.system_one.return_value = mock_resp

    report = client.diagnose_trajectory(sample_trajectory)

    assert report.loop_detected is True
    assert report.loop_type == LoopType.ONE_HOP_TOOL_REPEAT
    assert report.severity == 3
    assert report.culprit_tool == "cargo"


def test_diagnose_trajectory_healthy_with_typesafe():
    """Verifica que una trayectoria convergente sea calificada como saludable."""
    healthy_traj = Trajectory(
        session_id="healthy_session",
        goal="Resolver bug",
        steps=[
            Step(id="s1", step_type=StepType.THOUGHT, content="Inspeccionar archivo"),
            Step(id="s2", step_type=StepType.TOOL_CALL, tool_name="read_file", tool_args={"path": "main.py"}),
            Step(id="s3", step_type=StepType.TOOL_CALL, tool_name="finish", tool_args={"summary": "Listo"}),
        ],
    )

    cfg = PraxeonConfig(typesafe_api_key="mock_key", use_typesafe_api=True)
    client = TypeSafeJEVClient(cfg)
    client._client = MagicMock()

    mock_noul = MagicMock()
    mock_noul.noul = False

    mock_choice = MagicMock()
    mock_choice.choice = "none"

    mock_score = MagicMock()
    mock_score.score = 0

    mock_resp = MagicMock()
    mock_resp.nouls = {"has_loop_or_flaw": mock_noul}
    mock_resp.choices = {"loop_type": mock_choice}
    mock_resp.scores = {"severity": mock_score}

    client._client.system_one.return_value = mock_resp

    report = client.diagnose_trajectory(healthy_traj)

    assert report.loop_detected is False
    assert report.loop_type == LoopType.NONE
    assert report.severity == 0


def test_engine_delegates_diagnosis(sample_trajectory):
    """Verifica que JEVEngine delegue el diagnóstico directamente a TypeSafeJEVClient."""
    graph = StateGraph()
    graph.load_trajectory(sample_trajectory)
    engine = JEVEngine(graph)

    mock_report = LoopReport(
        loop_detected=True,
        loop_type=LoopType.HALLUCINATION,
        severity=4,
        explanation="Alucinación detectada por TypeSafe",
    )
    engine.typesafe_client = MagicMock()
    engine.typesafe_client.diagnose_trajectory.return_value = mock_report

    report = engine.diagnose_trajectory(sample_trajectory)
    assert report.loop_detected is True
    assert report.loop_type == LoopType.HALLUCINATION
    assert report.severity == 4
