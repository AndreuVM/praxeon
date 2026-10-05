"""Pruebas unitarias para el mecanismo de poda multidimensional (Fase 3 - F3-03).

Valida las 6 dimensiones de poda temprana:
- Seguridad y normativa (PolicyEngine BLOCK)
- Riesgo operacional (RiskEngine CRITICAL)
- Detección de bucles (LoopDetector)
- Faltante o contradicción de evidencias requeridas
- Límite de profundidad (Budget Exhaustion)
- Brecha y degradación de score
- Integración y sincronización con state.metadata["pruned_branches"]
"""

import pytest

from praxeon.domain import (
    ActionCandidate,
    BranchPath,
    BranchScore,
    BranchStatus,
    BranchStep,
    DecisionStatus,
    Goal,
    PolicyDecision,
    ToolCall,
)
from praxeon.reasoning import BranchPruner, PruningCategory
from praxeon.runtime.state import SessionState


@pytest.fixture
def base_session():
    goal = Goal(objective="Diagnosticar y reparar servicio de autenticación")
    return SessionState(session_id="sess_pruning_test", goal=goal)


def test_pruning_security_violation(base_session):
    """Valida la poda inmediata ante una violación de política de seguridad (BLOCK)."""
    pruner = BranchPruner()
    branch = BranchPath(branch_id="b_sec_01", root_session_id=base_session.session_id)

    # Paso bloqueado por política
    act_danger = ActionCandidate(
        id="act_rm",
        description="Eliminar directorio raíz",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "rm -rf /"}),
    )
    branch.add_step(
        BranchStep(
            step_id="s1",
            action=act_danger,
            decision=PolicyDecision(status=DecisionStatus.BLOCK, reason_codes=["DESTRUCTIVE_COMMAND"]),
        )
    )

    decision = pruner.evaluate_branch(branch, base_session)
    assert decision.should_prune is True
    assert decision.category == PruningCategory.SECURITY_VIOLATION
    assert "DESTRUCTIVE_COMMAND" in (decision.reason or "")

    # Aplicar poda
    applied = pruner.apply_pruning(branch, base_session, decision)
    assert applied is True
    assert branch.status == BranchStatus.PRUNED
    assert "b_sec_01" in base_session.metadata["pruned_branches"]


def test_pruning_critical_risk(base_session):
    """Valida la poda cuando la acción candidata entraña riesgo crítico."""
    pruner = BranchPruner()
    branch = BranchPath(branch_id="b_risk_01", root_session_id=base_session.session_id)

    # Acción de fork bomb
    act_bomb = ActionCandidate(
        id="act_bomb",
        description="Fork bomb peligroso",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": ":(){ :|:& };:"}),
    )

    decision = pruner.evaluate_branch(branch, base_session, candidate_action=act_bomb)
    assert decision.should_prune is True
    assert decision.category == PruningCategory.RISK_THRESHOLD
    assert decision.severity >= 4


def test_pruning_loop_detection(base_session):
    """Valida la poda temprana cuando se detecta un bucle repetitivo unihop."""
    pruner = BranchPruner()
    branch = BranchPath(branch_id="b_loop_01", root_session_id=base_session.session_id)

    act_step = ActionCandidate(
        id="act_repeat",
        description="Invocar comando fallido",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "curl http://broken.local"}),
    )
    branch.add_step(BranchStep(step_id="s1", action=act_step))

    # Candidato repite la misma acción idéntica inmediatamente
    cand_repeat = ActionCandidate(
        id="act_repeat_cand",
        description="Reintento idéntico",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "curl http://broken.local"}),
    )

    decision = pruner.evaluate_branch(branch, base_session, candidate_action=cand_repeat)
    assert decision.should_prune is True
    assert decision.category == PruningCategory.LOOP_DETECTED


def test_pruning_missing_required_evidence(base_session):
    """Valida la poda de acciones mutativas que carecen de evidencias requeridas."""
    pruner = BranchPruner()
    branch = BranchPath(branch_id="b_ev_01", root_session_id=base_session.session_id)

    # Acción que requiere evidencia previa sobre permisos del bucket
    act_deploy = ActionCandidate(
        id="act_deploy",
        description="Desplegar artefactos a producción",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "kubectl apply -f prod.yaml"}),
        requires_evidence=["ev_staging_approved", "ev_signature_verified"],
    )

    decision = pruner.evaluate_branch(branch, base_session, candidate_action=act_deploy)
    assert decision.should_prune is True
    assert decision.category == PruningCategory.EVIDENCE_CONTRADICTION
    assert "ev_staging_approved" in str(decision.details)


def test_pruning_budget_exhaustion(base_session):
    """Valida la poda cuando la rama sobrepasa la profundidad máxima autorizada."""
    pruner = BranchPruner(max_branch_depth=5)
    branch = BranchPath(branch_id="b_depth_01", root_session_id=base_session.session_id)

    for i in range(5):
        act = ActionCandidate(
            id=f"act_{i}",
            description=f"Lectura de archivo {i}",
            tool_call=ToolCall(tool_name="read_file", arguments={"path": f"f_{i}.txt"}),
        )
        branch.add_step(BranchStep(step_id=f"s_{i}", action=act))

    decision = pruner.evaluate_branch(branch, base_session)
    assert decision.should_prune is True
    assert decision.category == PruningCategory.BUDGET_EXHAUSTION
    assert "Profundidad máxima" in (decision.reason or "")


def test_pruning_score_degradation_and_relative_gap(base_session):
    """Valida la poda por degradación absoluta y por brecha excesiva respecto al mejor branch."""
    pruner = BranchPruner(min_composite_score=0.20, max_relative_gap=0.40)
    branch = BranchPath(branch_id="b_score_01", root_session_id=base_session.session_id)

    for i in range(2):
        act = ActionCandidate(
            id=f"act_{i}",
            description=f"Paso {i}",
            tool_call=ToolCall(tool_name="list_dir", arguments={"path": "."}),
        )
        branch.add_step(BranchStep(step_id=f"s_{i}", action=act))

    # 1. Poda por caída bajo min_composite_score
    branch.update_score(BranchScore(confidence=0.1, evidence_support=0.1, goal_alignment=0.1, risk_penalty=0.8))
    dec_low = pruner.evaluate_branch(branch, base_session)
    assert dec_low.should_prune is True
    assert dec_low.category == PruningCategory.SCORE_DEGRADATION

    # 2. Poda por brecha relativa excesiva con la mejor rama
    branch.update_score(BranchScore(confidence=0.5, evidence_support=0.5, goal_alignment=0.5))
    # Score de branch es ~0.45. Si la mejor rama tiene 0.95, brecha es 0.50 > 0.40
    dec_gap = pruner.evaluate_branch(branch, base_session, current_best_score=0.95)
    assert dec_gap.should_prune is True
    assert dec_gap.category == PruningCategory.SCORE_DEGRADATION
    assert "Brecha relativa excesiva" in (dec_gap.reason or "")
