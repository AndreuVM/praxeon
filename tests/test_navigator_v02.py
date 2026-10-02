"""Pruebas unitarias para Navigator como orquestador central en v0.2."""

import pytest
from praxeon.domain import (
    ActionCandidate,
    DecisionStatus,
    Goal,
    ToolCall,
)
from praxeon.providers.replay import ReplayProvider
from praxeon.runtime import Navigator, SecureExecutor, SessionState


def test_navigator_session_lifecycle_and_genesis_checkpoint():
    """Verifica el inicio de sesión y la creación del checkpoint génesis."""
    provider = ReplayProvider(default_scenario="safe_read")
    executor = SecureExecutor(dry_run=True)
    nav = Navigator(provider=provider, executor=executor)

    goal = Goal(objective="Diagnosticar logs", success_criteria=["error encontrado"])
    state = nav.start_session(goal, session_id="nav_sess_1")

    assert nav.state is not None
    assert nav.state.session_id == "nav_sess_1"
    assert len(nav.checkpoint_manager.list_checkpoints()) == 1
    assert nav.checkpoint_manager.list_checkpoints()[0].reason == "Genesis checkpoint"


def test_navigator_is_batchable_chunking_classification():
    """Verifica la clasificación estricta de acciones batchables vs aisladas."""
    provider = ReplayProvider(default_scenario="safe_read")
    nav = Navigator(provider=provider)

    read_act = ActionCandidate(
        id="act_r",
        description="Leer archivo",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "a.txt"}),
    )
    edit_act = ActionCandidate(
        id="act_w",
        description="Editar archivo",
        tool_call=ToolCall(tool_name="edit_file", arguments={"path": "a.txt", "content": "x"}),
    )
    cmd_act = ActionCandidate(
        id="act_cmd",
        description="Ejecutar comando",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "dir"}),
    )

    assert nav.is_batchable(read_act) is True
    assert nav.is_batchable(edit_act) is False
    assert nav.is_batchable(cmd_act) is False


def test_navigator_propose_filters_forbidden_tools():
    """Verifica que propose elimine candidatos que utilicen herramientas prohibidas."""
    provider = ReplayProvider(default_scenario="safe_read")
    nav = Navigator(provider=provider)
    nav.start_session(Goal(objective="test"))
    nav.state.forbid_tool("delete_file")

    acts = [
        ActionCandidate(
            id="a1",
            description="Borrar",
            tool_call=ToolCall(tool_name="delete_file", arguments={}),
        ),
        ActionCandidate(
            id="a2",
            description="Leer",
            tool_call=ToolCall(tool_name="read_file", arguments={}),
        ),
    ]

    filtered = nav.propose(acts)
    assert len(filtered) == 1
    assert filtered[0].id == "a2"


def test_navigator_step_authorized_execution_and_evidence():
    """Verifica el flujo normativo de step(): ALLOW -> Execution -> Evidence Ingest -> State Record."""
    provider = ReplayProvider(default_scenario="safe_read")
    executor = SecureExecutor(dry_run=True)
    nav = Navigator(provider=provider, executor=executor)
    nav.start_session(Goal(objective="test"))

    action = ActionCandidate(
        id="step_read",
        description="Leer archivo de configuración",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "config.yaml"}),
    )

    decision, observation = nav.step(action)

    assert decision.status == DecisionStatus.ALLOW
    assert observation is not None
    assert observation.success is True
    assert "[DRY-RUN]" in observation.output

    # Verificar que el paso y la evidencia se hayan registrado en el estado
    assert len(nav.state.steps) == 1
    assert nav.state.steps[0].action.id == "step_read"
    assert len(nav.state.evidence) > 0
    assert any("config.yaml" in ev.claim for ev in nav.state.evidence)

    # Verificar que se emitió el recibo de auditoría
    receipts = nav.get_receipts()
    assert len(receipts) == 1
    assert receipts[0].action_id == "step_read"
    assert receipts[0].decision == DecisionStatus.ALLOW


def test_navigator_step_auto_checkpoint_on_mutation():
    """Verifica que acciones mutantes creen un checkpoint previo automáticamente."""
    provider = ReplayProvider(default_scenario="safe_read")
    executor = SecureExecutor(dry_run=True)
    nav = Navigator(provider=provider, executor=executor)
    nav.start_session(Goal(objective="test"))

    assert len(nav.checkpoint_manager.list_checkpoints()) == 1  # Solo génesis

    edit_action = ActionCandidate(
        id="step_edit",
        description="Modificar auth.py",
        tool_call=ToolCall(tool_name="edit_file", arguments={"path": "auth.py", "content": "ok"}),
    )

    decision, observation = nav.step(edit_action, auto_checkpoint=True)

    assert decision.status == DecisionStatus.ALLOW
    # Debe haberse creado un checkpoint preventivo antes de la mutación
    checkpoints = nav.checkpoint_manager.list_checkpoints()
    assert len(checkpoints) == 2
    assert "Auto-checkpoint previo a mutación" in checkpoints[1].reason


def test_navigator_step_denied_prevents_physical_execution():
    """Verifica que acciones denegadas (BLOCK) no toquen el ejecutor físico."""
    provider = ReplayProvider(default_scenario="loop_detected")
    executor = SecureExecutor(dry_run=True)
    nav = Navigator(provider=provider, executor=executor)
    nav.start_session(Goal(objective="test"))

    # Herramienta no registrada en ToolRegistry
    bad_action = ActionCandidate(
        id="bad_act",
        description="Inyectar comando malicioso",
        tool_call=ToolCall(tool_name="unknown_exploit_tool", arguments={}),
    )

    decision, observation = nav.step(bad_action)

    assert decision.status in (DecisionStatus.BLOCK, DecisionStatus.REPLAN)
    assert observation is None
    assert len(nav.state.steps) == 1
    assert nav.state.steps[0].observation is None


def test_navigator_completion_verifier_integration():
    """Verifica que un intento de finish prematuro sea interceptado por el Navigator."""
    provider = ReplayProvider(default_scenario="safe_read")
    executor = SecureExecutor(dry_run=True)
    nav = Navigator(provider=provider, executor=executor)

    goal = Goal(
        objective="Generar reporte",
        success_criteria=["reporte generado en report.pdf"],
    )
    nav.start_session(goal)

    finish_action = ActionCandidate(
        id="finish",
        description="Finalizar tarea",
        tool_call=ToolCall(tool_name="finish", arguments={}),
    )

    decision, observation = nav.step(finish_action)

    # Debe ser denegado/replanteado porque faltan criterios
    assert decision.status == DecisionStatus.REPLAN
    assert any("UNVERIFIED_COMPLETION" in code for code in decision.reason_codes)
    assert observation is None


def test_navigator_rollback_restores_state_and_forbids_culprit():
    """Verifica el flujo formal de rollback ejecutado desde el Navigator."""
    provider = ReplayProvider(default_scenario="safe_read")
    executor = SecureExecutor(dry_run=True)
    nav = Navigator(provider=provider, executor=executor)
    nav.start_session(Goal(objective="test"))

    # Paso 1: Lectura exitosa
    nav.step(
        ActionCandidate(
            id="s1",
            description="Leer a.txt",
            tool_call=ToolCall(tool_name="read_file", arguments={"path": "a.txt"}),
        )
    )
    safe_chk = nav.checkpoint_manager.create_checkpoint(nav.state, reason="Safe step 1")

    # Paso 2: Paso posterior
    nav.step(
        ActionCandidate(
            id="s2",
            description="Buscar b",
            tool_call=ToolCall(tool_name="grep_search", arguments={"query": "b"}),
        )
    )
    assert len(nav.state.steps) == 2

    # Ejecutar rollback hacia el punto seguro
    restored = nav.rollback(
        checkpoint_id=safe_chk.id,
        culprit_tool="grep_search",
        reason="Paso 2 entró en bucle",
    )

    assert len(restored.steps) == 1
    assert "grep_search" in restored.forbidden_tools
    assert nav.state == restored
