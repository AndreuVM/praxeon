"""Pruebas unitarias para CompletionVerifier en v0.2."""

import pytest
from praxeon.domain import (
    ActionCandidate,
    Evidence,
    Goal,
    PolicyDecision,
    ToolCall,
)
from praxeon.reasoning import CompletionVerifier
from praxeon.runtime import SessionState


def test_completion_verifier_identifies_finish_actions():
    """Verifica que el detector reconozca acciones de finalización explícitas y declarativas."""
    verifier = CompletionVerifier()

    # Por tool_name
    act_tool = ActionCandidate(
        id="act_f1",
        description="Terminar",
        tool_call=ToolCall(tool_name="finish", arguments={}),
    )
    assert verifier.is_finish_action(act_tool) is True

    act_done = ActionCandidate(
        id="act_f2",
        description="Done",
        tool_call=ToolCall(tool_name="complete_task", arguments={}),
    )
    assert verifier.is_finish_action(act_done) is True

    # Por id o descripción
    act_desc = ActionCandidate(
        id="act_f3",
        description="Procedo a finalizar tarea y reportar éxito",
    )
    assert verifier.is_finish_action(act_desc) is True

    # Acción normal
    act_normal = ActionCandidate(
        id="act_read",
        description="Leer archivo de configuración",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "conf.json"}),
    )
    assert verifier.is_finish_action(act_normal) is False


def test_completion_verifier_rejects_premature_finish_zero_steps():
    """Verifica que rechace la finalización si no se ha ejecutado ningún paso."""
    verifier = CompletionVerifier()
    goal = Goal(objective="Migrar base de datos", success_criteria=["migración aplicada"])
    state = SessionState(session_id="s1", goal=goal)

    finish_action = ActionCandidate(
        id="finish",
        description="Finalizar tarea",
        tool_call=ToolCall(tool_name="finish", arguments={}),
    )

    assessment = verifier.verify(goal, state, finish_action)
    assert assessment.is_complete is False
    assert "Finalización prematura" in assessment.rationale
    assert "migración aplicada" in assessment.missing_criteria


def test_completion_verifier_rejects_when_criteria_missing():
    """Verifica que rechace finish si falta evidencia para alguno de los success_criteria."""
    verifier = CompletionVerifier()
    goal = Goal(
        objective="Implementar autenticación",
        success_criteria=["crear archivo auth.py", "todos los tests unitarios pasan"],
    )
    state = SessionState(session_id="s2", goal=goal)

    # Añadimos un paso y evidencia para el primer criterio pero no para los tests
    state.add_step(
        action=ActionCandidate(
            id="step1",
            description="Crear auth.py",
            tool_call=ToolCall(tool_name="edit_file", arguments={"path": "auth.py"}),
        ),
        decision=PolicyDecision(status="allow"),
        observation="Archivo auth.py creado exitosamente",
    )
    state.add_evidence(
        Evidence(
            id="ev1",
            claim="crear archivo auth.py",
            content_hash="h1",
            source_type="tool_observation",
        )
    )

    finish_action = ActionCandidate(
        id="finish",
        description="Finalizar tarea",
        tool_call=ToolCall(tool_name="finish", arguments={}),
    )

    assessment = verifier.verify(goal, state, finish_action)
    assert assessment.is_complete is False
    assert "crear archivo auth.py" in assessment.satisfied_criteria
    assert "todos los tests unitarios pasan" in assessment.missing_criteria
    assert assessment.confidence < 1.0


def test_completion_verifier_approves_when_all_criteria_satisfied():
    """Verifica que apruebe finish cuando todos los criterios cuentan con evidencia empírica."""
    verifier = CompletionVerifier()
    goal = Goal(
        objective="Corregir bug en parser",
        success_criteria=["bug parser corregido", "test_parser pasa"],
    )
    state = SessionState(session_id="s3", goal=goal)

    # Añadimos evidencias para ambos criterios
    state.add_step(
        action=ActionCandidate(id="s1", description="test"),
        decision=PolicyDecision(status="allow"),
        observation="test_parser ejecutado: 5 passed",
    )
    state.add_evidence(
        Evidence(
            id="ev1",
            claim="bug parser corregido en parser.py",
            content_hash="h1",
        )
    )
    state.add_evidence(
        Evidence(
            id="ev2",
            claim="test_parser pasa exitosamente",
            content_hash="h2",
        )
    )

    finish_action = ActionCandidate(
        id="finish",
        description="Finalizar tarea",
        tool_call=ToolCall(tool_name="finish", arguments={}),
    )

    assessment = verifier.verify(goal, state, finish_action)
    assert assessment.is_complete is True
    assert len(assessment.missing_criteria) == 0
    assert len(assessment.satisfied_criteria) == 2
    assert "Todos los criterios de éxito verificados" in assessment.rationale


def test_completion_verifier_blocks_on_forbidden_outcome():
    """Verifica que detecte violaciones de resultados prohibidos."""
    verifier = CompletionVerifier()
    goal = Goal(
        objective="Limpiar caché",
        success_criteria=["caché eliminada"],
        forbidden_outcomes=["archivo de producción borrado"],
    )
    state = SessionState(session_id="s4", goal=goal)

    # El agente borró producción accidentalmente
    state.add_step(
        action=ActionCandidate(id="s1", description="del"),
        decision=PolicyDecision(status="allow"),
        observation="Warning: archivo de producción borrado permanentemente",
    )
    state.add_evidence(
        Evidence(
            id="ev1",
            claim="caché eliminada",
            content_hash="h1",
        )
    )

    finish_action = ActionCandidate(
        id="finish",
        description="Finalizar tarea",
        tool_call=ToolCall(tool_name="finish", arguments={}),
    )

    assessment = verifier.verify(goal, state, finish_action)
    assert assessment.is_complete is False
    assert any("archivo de producción borrado" in v for v in assessment.unverified_claims)


def test_completion_verifier_avoids_false_positive_on_failed_log_containing_success_word():
    """Verifica que un log con 'Failed to verify success condition' o returncode=1 no sea interpretado como exitoso."""
    from praxeon.domain.goal import CriterionType, SuccessCriterion

    verifier = CompletionVerifier()
    goal = Goal(
        objective="Ejecutar suite de tests",
        criteria=[
            SuccessCriterion(
                id="c_tests",
                description="Tests unitarios pasan",
                criterion_type=CriterionType.TESTS_PASS,
            ),
            SuccessCriterion(
                id="c_exit",
                description="Comando finaliza con exit code 0",
                criterion_type=CriterionType.EXIT_CODE_ZERO,
            ),
        ],
    )
    state = SessionState(session_id="s_fail_log", goal=goal)

    # Observación de fallo que fortuitamente contiene la palabra "success"
    state.add_step(
        action=ActionCandidate(
            id="s1",
            description="run tests",
            tool_call=ToolCall(tool_name="run_command", arguments={"command": "pytest"}),
        ),
        decision=PolicyDecision(status="allow"),
        observation="FAILED: Failed to verify success condition. returncode=1. 2 failed, 0 passed.",
    )

    finish_action = ActionCandidate(
        id="finish",
        description="Finalizar tarea",
        tool_call=ToolCall(tool_name="finish", arguments={}),
    )

    assessment = verifier.verify(goal, state, finish_action)
    assert assessment.is_complete is False
    # Ambos criterios deben haber fallado o no estar verificados
    assert "c_tests" in assessment.missing_criteria or any(e.status.value == "failed" for e in assessment.evaluations if e.criterion_id == "c_tests")
    assert "c_exit" in assessment.missing_criteria or any(e.status.value == "failed" for e in assessment.evaluations if e.criterion_id == "c_exit")

