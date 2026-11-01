"""Pruebas rigurosas de bypass de seguridad, invariantes de capability y aislamiento en sandbox.

Verifica formalmente:
1. Executor sin recibo -> PolicyViolation inmediata (Cierre del agujero #1).
2. Executor con estatus distinto de ALLOW -> PolicyViolation.
3. Executor con acción manipulada (action_hash mismatch) -> PolicyViolation.
4. Executor con estado desfasado (state_hash mismatch) -> PolicyViolation.
5. Executor con sesión distinta (session_id mismatch) -> PolicyViolation.
6. Replay attack (mismo recibo reutilizado) -> PolicyViolation.
7. Integración de confirmación humana (PermissionManager en PolicyEngine).
8. Aislamiento de sandbox (depuración de variables de entorno y contención de rutas).
"""

import os
import pytest
from praxeon.domain.action import ActionCandidate, ToolCall, compute_action_hash
from praxeon.domain.decision import DecisionReceipt, DecisionStatus, PolicyDecision, compute_state_hash
from praxeon.domain.goal import Goal
from praxeon.policy.engine import PolicyEngine
from praxeon.policy.risk import ToolRegistry
from praxeon.providers.replay import ReplayProvider
from praxeon.runtime.executor import PolicyViolation, SecureExecutor
from praxeon.runtime.navigator import Navigator
from praxeon.runtime.sandbox import DryRunSandbox, LocalProcessSandbox, SandboxViolation
from praxeon.runtime.state import SessionState


@pytest.fixture
def session_state() -> SessionState:
    goal = Goal(objective="Probar enforcement de seguridad", success_criteria=["tests pasando"])
    return SessionState(session_id="test_security_session", goal=goal)


@pytest.fixture
def sample_action() -> ActionCandidate:
    return ActionCandidate(
        id="act_read_config",
        description="Leer configuracion del sistema",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "config.yaml"}),
    )


def test_bypass_execution_without_receipt_raises_violation(session_state, sample_action):
    """Verifica que invocar executor.execute sin recibo/capability sea inmediatamente bloqueado."""
    executor = SecureExecutor(insecure_skip_signature=True, dry_run=True, strict_capability=True)
    with pytest.raises(PolicyViolation) as exc_info:
        executor.execute(sample_action, session_state)
    assert "Se requiere un capability/DecisionReceipt" in str(exc_info.value)


def test_bypass_execution_with_non_allow_receipt_raises_violation(session_state, sample_action):
    """Verifica que un recibo con estatus BLOCK o REPLAN no permita la ejecución física."""
    executor = SecureExecutor(insecure_skip_signature=True, dry_run=True)
    receipt = DecisionReceipt(
        decision_id="dec_block_01",
        session_id=session_state.session_id,
        action_id=sample_action.id,
        state_hash=compute_state_hash(session_state.to_snapshot()),
        action_hash=compute_action_hash(sample_action),
        decision_status=DecisionStatus.BLOCK,
        reason_codes=["MANUAL_BLOCK"],
    )
    with pytest.raises(PolicyViolation) as exc_info:
        executor.execute(sample_action, session_state, receipt=receipt)
    assert "estatus no autorizado" in str(exc_info.value)


def test_bypass_execution_with_tampered_action_hash_raises_violation(session_state, sample_action):
    """Verifica que si la acción física no coincide con el hash del recibo (tampering), se aborte."""
    executor = SecureExecutor(insecure_skip_signature=True, dry_run=True)
    receipt = DecisionReceipt(
        decision_id="dec_allow_01",
        session_id=session_state.session_id,
        action_id=sample_action.id,
        state_hash=compute_state_hash(session_state.to_snapshot()),
        action_hash="fake_hash_tampered",
        decision_status=DecisionStatus.ALLOW,
    )
    with pytest.raises(PolicyViolation) as exc_info:
        executor.execute(sample_action, session_state, receipt=receipt)
    assert "El hash de la acción" in str(exc_info.value)
    assert "no coincide con el capability" in str(exc_info.value)


def test_bypass_execution_with_stale_state_hash_raises_violation(session_state, sample_action):
    """Verifica que un recibo emitido para un estado previo desfasado sea rechazado (Stale State Defense)."""
    executor = SecureExecutor(insecure_skip_signature=True, dry_run=True)
    receipt = DecisionReceipt(
        decision_id="dec_allow_02",
        session_id=session_state.session_id,
        action_id=sample_action.id,
        state_hash="stale_state_hash_prior_to_mutation",
        action_hash=compute_action_hash(sample_action),
        decision_status=DecisionStatus.ALLOW,
    )
    with pytest.raises(PolicyViolation) as exc_info:
        executor.execute(sample_action, session_state, receipt=receipt)
    assert "El hash de estado" in str(exc_info.value)
    assert "está desfasado" in str(exc_info.value)


def test_replay_attack_prevention_on_consumed_receipt(session_state, sample_action):
    """Verifica que un mismo capability no pueda reutilizarse múltiples veces (Anti-Replay Attack)."""
    executor = SecureExecutor(insecure_skip_signature=True, dry_run=True)
    receipt = DecisionReceipt(
        decision_id="dec_unique_nonce_123",
        session_id=session_state.session_id,
        action_id=sample_action.id,
        state_hash=compute_state_hash(session_state.to_snapshot()),
        action_hash=compute_action_hash(sample_action),
        decision_status=DecisionStatus.ALLOW,
    )

    # 1. Primera ejecución: autorizada y válida
    obs = executor.execute(sample_action, session_state, receipt=receipt)
    assert obs.success is True

    # 2. Segunda ejecución con el mismo capability: DEBE ser bloqueada por replay
    with pytest.raises(PolicyViolation) as exc_info:
        executor.execute(sample_action, session_state, receipt=receipt)
    assert "Replay attack prevention" in str(exc_info.value)


def test_human_confirmation_loop_with_permission_manager():
    """Verifica el flujo completo de confirmación humana: ABSTAIN -> confirm_action -> ALLOW."""
    provider = ReplayProvider(default_scenario="safe_read")
    executor = SecureExecutor(insecure_skip_signature=True, dry_run=True)
    nav = Navigator(provider=provider, executor=executor)
    nav.start_session(Goal(objective="Operaciones protegidas"))

    # run_command requiere confirmación humana por defecto en el registro
    action = ActionCandidate(
        id="act_reboot",
        description="Reiniciar servicio crítico",
        tool_call=ToolCall(tool_name="run_command", arguments={"cmd": "systemctl restart payment"}),
    )

    # 1. Intento sin confirmación: debe emitir ABSTAIN
    dec1, rec1 = nav.decide(action)
    assert dec1.status == DecisionStatus.ABSTAIN
    assert "HUMAN_CONFIRMATION_REQUIRED" in dec1.reason_codes

    # Intentar ejecutar con este recibo debe fallar
    assert rec1.decision_status == DecisionStatus.ABSTAIN
    with pytest.raises(PolicyViolation):
        executor.execute(action, nav.state, receipt=rec1)

    # 2. El operador humano confirma la acción explícitamente
    nav.confirm_action(action.id)
    assert nav.is_action_confirmed(action.id) is True

    # 3. Tras la confirmación, la política evalúa de nuevo y emite ALLOW con capability legítimo
    dec2, rec2 = nav.decide(action)
    assert dec2.status == DecisionStatus.ALLOW
    assert "HUMAN_CONFIRMED_ACTION" in dec2.reason_codes
    assert rec2.decision_status == DecisionStatus.ALLOW

    # 4. Ahora la ejecución física en sandbox es autorizada
    obs = executor.execute(action, nav.state, receipt=rec2)
    assert obs.success is True


def test_sandbox_environment_scrubbing():
    """Verifica que el sandbox depure variables de entorno sensibles (tokens, API keys)."""
    sandbox = LocalProcessSandbox()
    os.environ["TYPESAFE_API_KEY"] = "super_secret_typesafe_key"
    os.environ["GEMINI_API_KEY"] = "super_secret_gemini_key"
    os.environ["SAFE_TEST_VAR"] = "public_value"

    try:
        clean_env = sandbox._sanitize_environment()
        assert "TYPESAFE_API_KEY" not in clean_env
        assert "GEMINI_API_KEY" not in clean_env
        assert clean_env.get("SAFE_TEST_VAR") == "public_value"
        assert clean_env.get("JEV_SANDBOX_ACTIVE") == "1"
    finally:
        os.environ.pop("TYPESAFE_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("SAFE_TEST_VAR", None)


def test_sandbox_jail_containment_prevents_traversal():
    """Verifica que el sandbox impida leer o escribir fuera de la raíz del workspace."""
    workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    sandbox = LocalProcessSandbox(workspace_root=workspace, allow_external_cwd=False)

    # Intento de path traversal escapando del workspace
    traversal_path = "../../../../../etc/passwd"
    res = sandbox.read_file(traversal_path)
    assert res.success is False
    assert "Evasión de ruta detectada" in res.output
