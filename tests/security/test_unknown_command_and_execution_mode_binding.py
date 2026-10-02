"""Pruebas de seguridad para CHG-09 y CHG-10:
- CHG-09: Rechazo formal de herramientas/comandos desconocidos y enforcement E2E de modos de ejecución.
- CHG-10: Ligadura estricta de ExecutionMode a capabilities/recibos y mitigación contra manipulación de payload.
"""

from datetime import datetime, timedelta
import pytest

from praxeon.domain.action import compute_action_hash
from praxeon.domain.decision import (
    compute_state_hash,
    DecisionReceipt,
    DecisionStatus,
    ExecutionMode,
    sign_receipt,
)
from praxeon.domain.models import ActionCandidate, Goal, ToolCall
from praxeon.runtime.executor import PolicyViolation, SecureExecutor
from praxeon.runtime.state import SessionState
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
from praxeon.server.schemas.action import ProposeActionRequest


@pytest.fixture
def clean_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("PRAXEON_PROFILE", raising=False)
    monkeypatch.delenv("PRAXEON_ENV", raising=False)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "test_unknown_cmd_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


# ============================================================================
# CHG-09: End-to-end unknown command rejection and execution mode enforcement
# ============================================================================

def test_chg_09_unknown_tool_rejection_e2e_local_restricted(clean_runtime):
    """CHG-09: Pipeline E2E de rechazo de herramienta no registrada en LOCAL_RESTRICTED.
    
    Verifica que la secuencia:
    Unknown operation -> contextual classification -> evidence/risk/policy ->
    DecisionStatus.BLOCK -> capability status BLOCK -> SecureExecutor -> PolicyViolation.
    """
    session = clean_runtime.create_session(
        goal="Validación de comando desconocido",
        session_id="test_unknown_tool_sess",
        execution_mode="local_restricted",
    )

    # Proponer una herramienta desconocida / no registrada
    proposal = ProposeActionRequest(
        tool="unregistered_arbitrary_daemon",
        operation="spawn",
        arguments={"binary": "/bin/evil"},
        thought_rationale="Intento de ejecutar herramienta no catalogada",
    )

    resp = clean_runtime.propose_action(
        session_id="test_unknown_tool_sess",
        proposal=proposal,
    )

    # 1. Conforme a CHG-01, una herramienta desconocida no destructiva emite REVIEW (incertidumbre)
    # y exige confirmación obligatoria, sin emitir capability
    assert resp.status == "REVIEW"
    assert resp.policy.decision == "REQUIRE_HUMAN_CONFIRMATION"
    assert "UNKNOWN_TOOL_NOT_REGISTERED" in resp.policy.reason_codes
    assert resp.policy.requires_confirmation is True
    assert resp.capability is None

    # 2. El operador evalúa y RECHAZA formalmente la decisión
    reject_res = clean_runtime.confirm_decision(
        decision_id=resp.decision_id,
        approved=False,
        reason="Herramienta no autorizada rechazada explícitamente por el operador",
    )
    assert reject_res.status == "BLOCKED"
    assert reject_res.capability is None


    # 3. El recibo durable de auditoría almacenado debe reflejar estatus BLOCK
    decision_record = clean_runtime._decisions.get(resp.decision_id) or clean_runtime.decision_repository.get(resp.decision_id)
    assert decision_record is not None
    receipt = decision_record["receipt"]
    assert receipt is not None
    assert receipt.decision_status == DecisionStatus.BLOCK

    # 4. SecureExecutor debe bloquear la ejecución física con PolicyViolation si se le presenta el recibo de BLOCK
    action_candidate = decision_record["action"]
    state = clean_runtime.state_store.load_state("test_unknown_tool_sess")

    with pytest.raises(PolicyViolation) as exc_info:
        clean_runtime.executor.execute(
            action=action_candidate,
            state=state,
            receipt=receipt,
        )
    assert "estatus no autorizado: 'DecisionStatus.BLOCK'" in str(exc_info.value)

    # 5. La ejecución sin recibo también debe ser bloqueada inmediatamente
    with pytest.raises(PolicyViolation) as exc_no_receipt:
        clean_runtime.executor.execute(
            action=action_candidate,
            state=state,
            receipt=None,
        )
    assert "Se requiere un capability/DecisionReceipt" in str(exc_no_receipt.value)


def test_chg_09_unknown_tool_rejection_e2e_full_access(clean_runtime):
    """CHG-09 / CHG-02: En FULL_ACCESS (incluso autónomo), herramientas no catalogadas nunca se auto-aprueban;
    retienen obligatoriamente confirmación interactiva humana (REVIEW), y comandos destructivos son BLOCK."""
    session = clean_runtime.create_session(
        goal="Validación de Full Access contra herramientas no registradas",
        session_id="test_unknown_fa_sess",
        execution_mode="full_access",
        metadata={"autonomous": True, "allow_unattended_execution": True},
    )

    # A: Herramienta desconocida no destructiva -> retiene REVIEW, nunca ALLOW
    proposal_innocuous = ProposeActionRequest(
        tool="unregistered_custom_script",
        operation="run",
        arguments={"path": "custom.py"},
        thought_rationale="Intento en full_access",
    )
    resp_innocuous = clean_runtime.propose_action(
        session_id="test_unknown_fa_sess",
        proposal=proposal_innocuous,
    )
    assert resp_innocuous.status == "REVIEW"
    assert resp_innocuous.policy.requires_confirmation is True
    assert "UNKNOWN_TOOL_NOT_REGISTERED" in resp_innocuous.policy.reason_codes
    assert resp_innocuous.capability is None

    # B: Herramienta desconocida con comando destructivo -> BLOCK incondicional
    proposal_destructive = ProposeActionRequest(
        tool="unregistered_wiper",
        arguments={"command": "rm -rf / --no-preserve-root"},
        thought_rationale="Intento destructivo en full_access",
    )
    resp_destructive = clean_runtime.propose_action(
        session_id="test_unknown_fa_sess",
        proposal=proposal_destructive,
    )
    assert resp_destructive.status == "BLOCK"
    assert resp_destructive.capability is None



def test_chg_09_secure_executor_barrier_blocks_even_if_allow_forged():
    """CHG-09: Si un atacante forjara un recibo con ALLOW para una herramienta desconocida,
    la barrera 3 de SecureExecutor rechaza la ejecución sin alcanzar el host.
    """
    secret = "secret-barrier-test"
    executor = SecureExecutor(dry_run=True, secret_key=secret)

    goal = Goal(objective="Test barrier 3")
    state = SessionState(session_id="barrier_test_sess", goal=goal)

    unknown_action = ActionCandidate(
        id="act_unknown_barrier",
        description="Herramienta desconocida forjada",
        tool_call=ToolCall(tool_name="phantom_tool", arguments={"arg": "val"}),
    )

    # Forjar recibo con ALLOW firmado correctamente pero para herramienta desconocida
    receipt = DecisionReceipt(
        decision_id="dec_phantom_forged",
        action_id=unknown_action.id,
        action_hash=compute_action_hash(unknown_action),
        state_hash=state.compute_hash(),
        session_id=state.session_id,
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )
    receipt = sign_receipt(receipt, secret)

    with pytest.raises(PolicyViolation) as exc_info:
        executor.execute(action=unknown_action, state=state, receipt=receipt)

    assert "herramienta desconocida 'phantom_tool' no admitida en ToolRegistry" in str(exc_info.value)


# ============================================================================
# CHG-10: Capability execution_mode binding enforcement
# ============================================================================

def test_chg_10_execution_mode_mismatch_restricted_capability_on_full_access():
    """CHG-10: Un capability emitido para LOCAL_RESTRICTED no puede ejecutarse en FULL_ACCESS."""
    secret = "test-execution-mode-secret"
    executor = SecureExecutor(dry_run=True, secret_key=secret, allow_full_access=True)

    goal = Goal(objective="Test mode binding")
    # Estado de sesión configurado para FULL_ACCESS
    state_fa = SessionState(
        session_id="sess_mode_bind_1",
        goal=goal,
        metadata={"execution_mode": ExecutionMode.FULL_ACCESS.value},
    )

    action = ActionCandidate(
        id="act_read",
        description="Lectura permitida",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "safe.txt"}),
    )

    # Capability emitido explícitamente para LOCAL_RESTRICTED
    receipt = DecisionReceipt(
        decision_id="dec_mode_restr",
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=state_fa.compute_hash(),
        session_id=state_fa.session_id,
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )
    receipt = sign_receipt(receipt, secret)

    with pytest.raises(PolicyViolation) as exc_info:
        executor.execute(action=action, state=state_fa, receipt=receipt)

    assert "Mismatch de ExecutionMode" in str(exc_info.value)
    assert "El capability fue firmado para 'local_restricted' pero la sesión requiere 'full_access'" in str(exc_info.value)


def test_chg_10_execution_mode_mismatch_full_access_capability_on_restricted():
    """CHG-10: Un capability emitido para FULL_ACCESS no puede ejecutarse en LOCAL_RESTRICTED."""
    secret = "test-execution-mode-secret"
    executor = SecureExecutor(dry_run=True, secret_key=secret)

    goal = Goal(objective="Test mode binding reverse")
    state_restr = SessionState(
        session_id="sess_mode_bind_2",
        goal=goal,
        metadata={"execution_mode": ExecutionMode.LOCAL_RESTRICTED.value},
    )

    action = ActionCandidate(
        id="act_read",
        description="Lectura permitida",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "safe.txt"}),
    )

    # Capability firmado para FULL_ACCESS
    receipt = DecisionReceipt(
        decision_id="dec_mode_fa",
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=state_restr.compute_hash(),
        session_id=state_restr.session_id,
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.FULL_ACCESS.value,
    )
    receipt = sign_receipt(receipt, secret)

    with pytest.raises(PolicyViolation) as exc_info:
        executor.execute(action=action, state=state_restr, receipt=receipt)

    assert "Mismatch de ExecutionMode" in str(exc_info.value)
    assert "El capability fue firmado para 'full_access' pero la sesión requiere 'local_restricted'" in str(exc_info.value)


def test_chg_10_tampered_action_payload_rejection():
    """CHG-10: Alteración de argumentos o payload de acción tras emisión del capability."""
    secret = "test-tamper-secret"
    executor = SecureExecutor(dry_run=True, secret_key=secret)

    goal = Goal(objective="Test payload tampering")
    state = SessionState(session_id="sess_tamper", goal=goal)

    original_action = ActionCandidate(
        id="act_orig",
        description="Leer archivo permitido",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "public.txt"}),
    )

    receipt = DecisionReceipt(
        decision_id="dec_orig",
        action_id=original_action.id,
        action_hash=compute_action_hash(original_action),
        state_hash=state.compute_hash(),
        session_id=state.session_id,
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )
    receipt = sign_receipt(receipt, secret)

    # Acción manipulada con argumentos cambiados a archivo sensible
    tampered_action = ActionCandidate(
        id="act_orig",
        description="Leer archivo permitido",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "sensitive_credentials.env"}),
    )

    with pytest.raises(PolicyViolation) as exc_info:
        executor.execute(action=tampered_action, state=state, receipt=receipt)

    assert "El hash de la acción" in str(exc_info.value)
    assert "no coincide con el capability" in str(exc_info.value)


def test_chg_10_expired_capability_rejection():
    """CHG-10: Un capability expirado en tiempo es rechazado inmediatamente."""
    secret = "test-expiry-secret"
    executor = SecureExecutor(dry_run=True, secret_key=secret)

    goal = Goal(objective="Test expiry")
    state = SessionState(session_id="sess_expiry", goal=goal)

    action = ActionCandidate(
        id="act_exp",
        description="Acción expirada",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "public.txt"}),
    )

    # Capability expirado hace 10 segundos
    receipt = DecisionReceipt(
        decision_id="dec_exp",
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=state.compute_hash(),
        session_id=state.session_id,
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
        expires_at=datetime.utcnow() - timedelta(seconds=10),
    )
    receipt = sign_receipt(receipt, secret)

    with pytest.raises(PolicyViolation) as exc_info:
        executor.execute(action=action, state=state, receipt=receipt)

    assert "El capability ha expirado" in str(exc_info.value)


def test_chg_10_session_mismatch_rejection():
    """CHG-10: Un capability emitido para la sesión A presentado en la sesión B es rechazado."""
    secret = "test-session-mismatch-secret"
    executor = SecureExecutor(dry_run=True, secret_key=secret)

    goal = Goal(objective="Test session binding")
    state_a = SessionState(session_id="sess_A", goal=goal)
    state_b = SessionState(session_id="sess_B", goal=goal)

    action = ActionCandidate(
        id="act_sess",
        description="Acción de prueba",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "test.txt"}),
    )

    # Capability emitido para Session A (pero con hash de estado coincidente para aislar la validación de session_id)
    receipt_a = DecisionReceipt(
        decision_id="dec_sess_a",
        action_id=action.id,
        action_hash=compute_action_hash(action),
        state_hash=state_b.compute_hash(),
        session_id=state_a.session_id,
        decision_status=DecisionStatus.ALLOW,
        execution_mode=ExecutionMode.LOCAL_RESTRICTED.value,
    )
    receipt_a = sign_receipt(receipt_a, secret)

    # Ejecutar en Session B
    with pytest.raises(PolicyViolation) as exc_info:
        executor.execute(action=action, state=state_b, receipt=receipt_a)

    assert "El ID de sesión del capability (sess_A) no coincide con la sesión activa (sess_B)" in str(exc_info.value)
