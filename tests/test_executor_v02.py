"""Pruebas unitarias para SecureExecutor y PolicyViolation en v0.2."""

import pytest
from praxeon.domain import (
    ActionCandidate,
    DecisionReceipt,
    DecisionStatus,
    Goal,
    PolicyDecision,
    ToolCall,
    compute_action_hash,
)
from praxeon.runtime import PolicyViolation, SecureExecutor, SessionState


def test_executor_blocks_unauthorized_decision_with_policy_violation():
    """Verifica que el ejecutor lance PolicyViolation físicamente si la decisión no es ALLOW."""
    executor = SecureExecutor(insecure_skip_signature=True)
    state = SessionState(session_id="s1", goal=Goal(objective="test"))

    action = ActionCandidate(
        id="act_1",
        description="Borrar base de datos",
        tool_call=ToolCall(tool_name="delete_file", arguments={"path": "/db"}),
    )

    denied_statuses = [DecisionStatus.BLOCK, DecisionStatus.REPLAN, DecisionStatus.ABSTAIN]
    for status in denied_statuses:
        receipt = DecisionReceipt(
            decision_id=f"dec_{status.value}",
            session_id=state.session_id,
            action_id=action.id,
            action_hash=compute_action_hash(action),
            state_hash=state.compute_hash(),
            decision_status=status,
            reason_codes=["TEST_DENIAL"],
        )
        with pytest.raises(PolicyViolation) as exc_info:
            executor.execute(action, state=state, receipt=receipt)

        assert f"estatus no autorizado: '{status}'" in str(exc_info.value)
        assert exc_info.value.action == action

    # Modo compatibilidad con strict_capability=False
    compat_executor = SecureExecutor(insecure_skip_signature=True, strict_capability=False)
    for status in denied_statuses:
        decision = PolicyDecision(status=status, reason_codes=["TEST_DENIAL"])
        with pytest.raises(PolicyViolation) as exc_info:
            compat_executor.execute(action, state=state, decision=decision)

        assert f"el estatus de la política es {status}" in str(exc_info.value)
        assert exc_info.value.action == action
        assert exc_info.value.decision == decision


def test_executor_blocks_forbidden_tool_in_state():
    """Verifica que si la herramienta está prohibida en el estado, se aborte con PolicyViolation."""
    executor = SecureExecutor(insecure_skip_signature=True)
    state = SessionState(session_id="s2", goal=Goal(objective="test"))
    state.forbid_tool("run_command")

    action = ActionCandidate(
        id="act_2",
        description="Ejecutar comando prohibido",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "dir"}),
    )
    receipt = DecisionReceipt(
        decision_id="dec_allow_2",
        session_id=state.session_id,
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=state.compute_hash(),
        decision_status=DecisionStatus.ALLOW,
    )

    with pytest.raises(PolicyViolation) as exc_info:
        executor.execute(action, state=state, receipt=receipt)

    assert "explícitamente PROHIBIDA" in str(exc_info.value)


def test_executor_blocks_unknown_tool_not_in_registry():
    """Verifica que herramientas no registradas sean bloqueadas físicamente."""
    executor = SecureExecutor(insecure_skip_signature=True)
    state = SessionState(session_id="s3", goal=Goal(objective="test"))

    action = ActionCandidate(
        id="act_3",
        description="Herramienta inventada",
        tool_call=ToolCall(tool_name="unregistered_malicious_plugin", arguments={}),
    )
    receipt = DecisionReceipt(
        decision_id="dec_allow_3",
        session_id=state.session_id,
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=state.compute_hash(),
        decision_status=DecisionStatus.ALLOW,
    )

    with pytest.raises(PolicyViolation) as exc_info:
        executor.execute(action, state=state, receipt=receipt)

    assert "herramienta desconocida" in str(exc_info.value)


def test_executor_executes_authorized_action_dry_run():
    """Verifica la ejecución en modo dry_run sin efectos secundarios."""
    executor = SecureExecutor(insecure_skip_signature=True, dry_run=True)
    state = SessionState(session_id="s4", goal=Goal(objective="test"))

    action = ActionCandidate(
        id="act_dry",
        description="Lectura simulada",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "dummy.txt"}),
    )
    receipt = DecisionReceipt(
        decision_id="dec_allow_4",
        session_id=state.session_id,
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=state.compute_hash(),
        decision_status=DecisionStatus.ALLOW,
    )

    obs = executor.execute(action, state=state, receipt=receipt)
    assert obs.success is True
    assert "[DRY-RUN" in obs.output
    assert obs.tool_name == "read_file"


def test_executor_custom_handler_injection():
    """Verifica que se puedan inyectar controladores mock/personalizados para herramientas."""
    executor = SecureExecutor(insecure_skip_signature=True)
    state = SessionState(session_id="s5", goal=Goal(objective="test"))

    def mock_fetch(args):
        return f"Mocked URL content for {args.get('url')}"

    executor.register_handler("fetch_url", mock_fetch)

    action = ActionCandidate(
        id="act_custom",
        description="Obtener URL",
        tool_call=ToolCall(tool_name="fetch_url", arguments={"url": "https://api.test"}),
    )
    receipt = DecisionReceipt(
        decision_id="dec_allow_5",
        session_id=state.session_id,
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=state.compute_hash(),
        decision_status=DecisionStatus.ALLOW,
    )

    obs = executor.execute(action, state=state, receipt=receipt)
    assert obs.success is True
    assert "Mocked URL content" in obs.output
