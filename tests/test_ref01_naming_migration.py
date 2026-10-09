"""Test de validación para REF-01: Migración de naming histórico a canónico."""

import pytest
from praxeon.core import DecisionEngine, JEVEngine, PraxeonEngine
from praxeon.core.decision_engine import DecisionEngine as DirectDecisionEngine
from praxeon.core.jev_engine import JEVEngine as LegacyJEVEngine
from praxeon.models import DecisionScore, JEVScore
from praxeon.models.schema import DecisionScore as DirectDecisionScore
from praxeon.dashboard import PraxeonDashboard, JEVDashboard
from praxeon.core.state_graph import StateGraph
from praxeon.config import default_config


def test_decision_engine_canonical_and_backward_compatibility():
    # 1. Identidad de clases
    assert DecisionEngine is DirectDecisionEngine
    assert JEVEngine is DecisionEngine
    assert PraxeonEngine is DecisionEngine
    assert LegacyJEVEngine is DecisionEngine

    # 2. Instanciación y métodos
    graph = StateGraph(default_config)
    engine = DecisionEngine(graph, default_config)
    assert isinstance(engine, DecisionEngine)
    assert isinstance(engine, JEVEngine)


def test_decision_score_canonical_and_backward_compatibility():
    # 1. Identidad de clases
    assert DecisionScore is DirectDecisionScore
    assert JEVScore is DecisionScore

    # 2. Instanciación con campos y alias property
    score = DecisionScore(
        candidate_id="cand_1",
        p_progress=0.85,
        delta_u=0.70,
        loop_penalty=0.0,
        total_jev=0.775,
        details={"model": "test"},
    )
    assert score.total_jev == 0.775
    assert score.total_score == 0.775
    assert score.candidate_id == "cand_1"

    # 3. Serialización Pydantic
    dumped = score.model_dump()
    assert "total_jev" in dumped
    assert dumped["p_progress"] == 0.85


def test_dashboard_canonical_and_backward_compatibility():
    # 1. Identidad de clases
    assert PraxeonDashboard is not None
    assert JEVDashboard is PraxeonDashboard

    # 2. Instanciación
    dash = PraxeonDashboard(goal="Probar migración canónica REF-01")
    assert isinstance(dash, PraxeonDashboard)
    assert isinstance(dash, JEVDashboard)
    assert dash.goal == "Probar migración canónica REF-01"
