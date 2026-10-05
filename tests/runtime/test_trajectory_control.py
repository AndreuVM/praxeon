"""Pruebas unitarias para el Control Adaptativo de Trayectorias (Fase 8 - F8-03).

Valida la emisión en vuelo de directivas de supervisión:
- CONTINUE: Avance secuencial en pasos nominales.
- TERMINATE: Detección y verificación de completitud de la meta.
- PRUNE: Poda determinista ante bucles repetitivos de herramientas.
- RECOVER: Retroceso correctivo ante errores transitorios.
- ESCALATE: Escalado automático ante errores persistentes o incertidumbre epistémica elevada.
- SUSPEND_HITL: Freno y suspensión humana ante violaciones de contención o políticas.
"""

import pytest

from praxeon.domain.assessment import RiskLevel
from praxeon.routing.models import TaskRequirement
from praxeon.runtime.adaptive.models import (
    StepDispatchSpec,
    TrajectoryDirective,
    TrajectoryStepRecord,
)
from praxeon.runtime.adaptive.trajectory_controller import TrajectoryController


@pytest.fixture
def controller():
    return TrajectoryController(uncertainty_threshold=0.70, max_consecutive_errors=2)


@pytest.fixture
def sample_spec():
    return StepDispatchSpec(
        step_index=0,
        agent_id="ag_dev",
        assigned_role="Developer",
        model_name="gemini-1.5-pro",
        minimal_context_fingerprint="fp12345",
        authorized_tools=["view_file", "write_file", "run_command"],
    )


@pytest.fixture
def sample_task():
    return TaskRequirement(
        task_id="t_traj_01",
        prompt="Build resilient stream processor",
    )


def test_trajectory_directive_continue_on_nominal_step(controller, sample_spec, sample_task):
    """Valida que un paso nominal seguro emita CONTINUE."""
    step = TrajectoryStepRecord(
        step_id="s1",
        step_index=0,
        dispatch_spec=sample_spec,
        action_proposed={"tool_name": "view_file", "arguments": {"path": "main.py"}},
        policy_decision="ALLOW",
        observation="File content loaded successfully.",
    )

    directive, rationale = controller.evaluate_step(step, history=[], task=sample_task)
    assert directive == TrajectoryDirective.CONTINUE
    assert "Avanzando" in rationale


def test_trajectory_directive_terminate_on_completion(controller, sample_spec, sample_task):
    """Valida que la presencia de marcadores de completitud emita TERMINATE."""
    step = TrajectoryStepRecord(
        step_id="s_final",
        step_index=3,
        dispatch_spec=sample_spec,
        action_proposed={"tool_name": "run_command", "arguments": {"command": "pytest"}},
        policy_decision="ALLOW",
        observation="All unit tests passed. task_completed.",
        metadata={"is_complete": True},
    )

    directive, rationale = controller.evaluate_step(step, history=[], task=sample_task)
    assert directive == TrajectoryDirective.TERMINATE
    assert "completada" in rationale.lower()


def test_trajectory_directive_prune_on_tool_loop(controller, sample_spec, sample_task):
    """Valida que invocar la misma acción 3 veces consecutivas sea podado como bucle (PRUNE)."""
    action = {"tool_name": "grep_search", "arguments": {"pattern": "TODO"}}

    prev1 = TrajectoryStepRecord(
        step_id="s1", step_index=1, dispatch_spec=sample_spec,
        action_proposed=action, observation="Found 0 matches"
    )
    prev2 = TrajectoryStepRecord(
        step_id="s2", step_index=2, dispatch_spec=sample_spec,
        action_proposed=action, observation="Found 0 matches"
    )
    curr = TrajectoryStepRecord(
        step_id="s3", step_index=3, dispatch_spec=sample_spec,
        action_proposed=action, observation="Found 0 matches"
    )

    directive, rationale = controller.evaluate_step(curr, history=[prev1, prev2], task=sample_task)
    assert directive == TrajectoryDirective.PRUNE
    assert "Bucle de herramientas detectado" in rationale


def test_trajectory_directive_recover_on_transient_error(controller, sample_spec, sample_task):
    """Valida que un primer error de ejecución active retroceso a checkpoint (RECOVER)."""
    step = TrajectoryStepRecord(
        step_id="s_err",
        step_index=1,
        dispatch_spec=sample_spec,
        action_proposed={"tool_name": "run_command", "arguments": {"command": "python app.py"}},
        observation="Traceback (most recent call last): SyntaxError in line 12",
    )

    directive, rationale = controller.evaluate_step(step, history=[], task=sample_task)
    assert directive == TrajectoryDirective.RECOVER
    assert "retroceso determinista" in rationale.lower()


def test_trajectory_directive_escalate_on_repeated_errors(controller, sample_spec, sample_task):
    """Valida que errores consecutivos reiterados disparen escalado supervisado (ESCALATE)."""
    prev_err = TrajectoryStepRecord(
        step_id="s_err1",
        step_index=1,
        dispatch_spec=sample_spec,
        action_proposed={"tool_name": "run_command", "arguments": {"command": "python app.py"}},
        observation="Exception: connection refused",
    )
    curr_err = TrajectoryStepRecord(
        step_id="s_err2",
        step_index=2,
        dispatch_spec=sample_spec,
        action_proposed={"tool_name": "run_command", "arguments": {"command": "python app.py"}},
        observation="Exception: connection refused",
    )

    directive, rationale = controller.evaluate_step(curr_err, history=[prev_err], task=sample_task)
    assert directive == TrajectoryDirective.ESCALATE
    assert "Errores de ejecución persistentes" in rationale


def test_trajectory_directive_escalate_on_high_uncertainty(controller, sample_spec, sample_task):
    """Valida que un score de incertidumbre epistémica elevado dispare ESCALATE."""
    step = TrajectoryStepRecord(
        step_id="s_unc",
        step_index=1,
        dispatch_spec=sample_spec,
        action_proposed={"tool_name": "view_file", "arguments": {"path": "algo.py"}},
        observation="Algorithm has 3 diverging theoretical interpretations.",
        metadata={"uncertainty_score": 0.85},
    )

    directive, rationale = controller.evaluate_step(step, history=[], task=sample_task)
    assert directive == TrajectoryDirective.ESCALATE
    assert "Incertidumbre epistémica excesiva" in rationale


def test_trajectory_directive_suspend_hitl_on_security_denial(controller, sample_spec, sample_task):
    """Valida que una acción denegada por política de seguridad fuerce la suspensión humana (SUSPEND_HITL)."""
    step = TrajectoryStepRecord(
        step_id="s_deny",
        step_index=2,
        dispatch_spec=sample_spec,
        action_proposed={"tool_name": "rm -rf", "arguments": {"path": "/var"}},
        policy_decision="DENY",
        observation="Policy Denial: Dangerous destructive pattern",
    )

    directive, rationale = controller.evaluate_step(step, history=[], task=sample_task)
    assert directive == TrajectoryDirective.SUSPEND_HITL
    assert "bloqueado por política de seguridad" in rationale.lower()
