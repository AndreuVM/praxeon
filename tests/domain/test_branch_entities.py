"""Pruebas unitarias para las entidades de búsqueda y bifurcación (Fase 3 - F3-01).

Valida:
- Tipado, inmutabilidad y cálculo compuesto de BranchScore.
- Creación y serialización de BranchStep y RollbackCheckpoint.
- Ciclo de vida completo de BranchPath (add_step, mark_pruned, mark_committed, etc.).
"""

import pytest
from pydantic import ValidationError

from praxeon.domain import (
    ActionCandidate,
    BranchPath,
    BranchScore,
    BranchStatus,
    BranchStep,
    DecisionStatus,
    PolicyDecision,
    RollbackCheckpoint,
    ToolCall,
)


def test_branch_score_calculation_and_immutability():
    """Valida la inmutabilidad y el cálculo de la heurística multidimensional de BranchScore."""
    score = BranchScore(
        confidence=0.9,
        evidence_support=0.8,
        risk_penalty=0.1,
        goal_alignment=0.85,
        depth_penalty=0.05,
    )

    # Inmutabilidad
    with pytest.raises(ValidationError):
        score.confidence = 0.5  # type: ignore

    # Ponderaciones:
    # 0.9 * 0.25 (0.225) + 0.8 * 0.30 (0.24) + 0.85 * 0.35 (0.2975) - 0.1 * 0.40 (0.04) - 0.05 = 0.6725
    expected = round(0.225 + 0.24 + 0.2975 - 0.04 - 0.05, 4)
    assert score.composite_score == expected
    assert 0.0 <= score.composite_score <= 1.0


def test_branch_score_bounds():
    """Valida que el composite score no se desborde fuera de [0.0, 1.0]."""
    # Caso peor (riesgo extremo y penalización)
    worst = BranchScore(
        confidence=0.0,
        evidence_support=0.0,
        risk_penalty=1.0,
        goal_alignment=0.0,
        depth_penalty=2.0,
    )
    assert worst.composite_score == 0.0

    # Caso perfecto
    best = BranchScore(
        confidence=1.0,
        evidence_support=1.0,
        risk_penalty=0.0,
        goal_alignment=1.0,
        depth_penalty=0.0,
    )
    # 0.25 + 0.30 + 0.35 = 0.90
    assert best.composite_score == 0.90


def test_branch_step_and_checkpoint():
    """Valida creación y serialización de BranchStep y RollbackCheckpoint."""
    action = ActionCandidate(
        id="act_01",
        description="Exploración de archivo de configuración",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "config.yaml"}),
    )
    decision = PolicyDecision(status=DecisionStatus.ALLOW)

    step = BranchStep(
        step_id="bstep_1",
        action=action,
        decision=decision,
        observation="cluster: staging\nenv: prod",
        is_speculative=True,
        step_score=0.85,
        token_estimate=45,
    )

    step_dict = step.to_dict()
    assert step_dict["step_id"] == "bstep_1"
    assert step_dict["is_speculative"] is True
    assert step_dict["step_score"] == 0.85
    assert step_dict["token_estimate"] == 45

    checkpoint = RollbackCheckpoint(
        checkpoint_id="chk_01",
        branch_id="branch_alpha",
        session_id="sess_tree_01",
        step_index=1,
        state_snapshot_hash="hash_state_abc",
        evidence_ids=["ev_1", "ev_2"],
        forbidden_tools=["bash", "rm"],
    )

    chk_dict = checkpoint.to_dict()
    assert chk_dict["checkpoint_id"] == "chk_01"
    assert chk_dict["branch_id"] == "branch_alpha"
    assert "ev_1" in chk_dict["evidence_ids"]
    assert "rm" in chk_dict["forbidden_tools"]


def test_branch_path_lifecycle():
    """Valida transiciones de estado, agregado de pasos y cálculo de métricas en BranchPath."""
    branch = BranchPath(
        branch_id="b_test_01",
        parent_branch_id=None,
        root_session_id="sess_root",
        status=BranchStatus.EXPLORING,
    )

    assert branch.depth == 0
    assert len(branch.actions) == 0

    # Agregar 2 pasos
    act1 = ActionCandidate(
        id="act_1",
        description="Paso 1",
        tool_call=ToolCall(tool_name="list_dir", arguments={"path": "."}),
    )
    act2 = ActionCandidate(
        id="act_2",
        description="Paso 2",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "main.py"}),
    )

    branch.add_step(BranchStep(step_id="s1", action=act1, is_speculative=True))
    branch.add_step(BranchStep(step_id="s2", action=act2, is_speculative=True))

    assert branch.depth == 2
    assert len(branch.actions) == 2
    assert branch.actions[0].id == "act_1"

    # Actualizar puntuación
    branch.update_score(BranchScore(confidence=0.9, evidence_support=0.9))
    assert branch.score.confidence == 0.9

    # Probar poda formal
    branch.mark_pruned("Violación de política de seguridad detectada en paso s2")
    assert branch.status == BranchStatus.PRUNED
    assert "Violación de política" in (branch.prune_reason or "")

    # Probar commit
    branch.mark_committed()
    assert branch.status == BranchStatus.COMMITTED

    # Probar serialización
    data = branch.to_dict()
    assert data["branch_id"] == "b_test_01"
    assert data["status"] == "COMMITTED"
    assert data["depth"] == 2
    assert data["step_count"] == 2
    assert len(data["steps"]) == 2
