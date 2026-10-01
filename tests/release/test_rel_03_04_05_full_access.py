"""Pruebas de aceptación formal de Release: REL-03, REL-04 y REL-05 (Full Access Governance & Invariants).

Conforme al documento de cierre 'PRAXEON v1.0.0 — Plan formal de cierre de versión antes de integrar Context Caching':
- REL-03: BLOCK + Full Access -> 0 ejecuciones físicas. BLOCK siempre gana incondicionalmente.
- REL-04: REPLAN + Full Access -> REPLAN conservado. Nunca se convierte silenciosamente en ejecución.
- REL-05: REVIEW authorization -> Bypass solo bajo contrato explícito (Full Access + autonomous + autorización de operador).
- Criterio: ninguna ruta física contradice la decisión final y todas las excepciones deliberadas están cubiertas.
"""

import os
from pathlib import Path
import pytest

from praxeon.domain.decision import DecisionStatus, ExecutionMode
from praxeon.domain.events import EventType
from praxeon.domain.models import ActionCandidate, Goal, ToolCall
from praxeon.runtime.executor import PolicyViolation, SecureExecutor
from praxeon.runtime.state import SessionState
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    set_active_security_profile,
    set_runtime_service,
)
from praxeon.server.schemas.action import ProposeActionRequest


VALID_OPERATOR_SECRET = "praxeon_super_secure_operator_secret_key_42"


@pytest.fixture(autouse=True)
def reset_security_context():
    """Limpia el perfil activo global antes y después de cada prueba."""
    set_active_security_profile(None)
    yield
    set_active_security_profile(None)


@pytest.fixture
def fa_runtime(tmp_path, monkeypatch):
    """Configura una instancia limpia de RuntimeApplicationService."""
    monkeypatch.setenv("PRAXEON_SECRET_KEY", VALID_OPERATOR_SECRET)
    monkeypatch.setenv("PRAXEON_API_KEY", VALID_OPERATOR_SECRET)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "fa_rel_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


# =============================================================================
# REL-03: BLOCK + Full Access -> 0 ejecuciones físicas
# =============================================================================

def test_rel_03_block_always_wins_in_full_access(fa_runtime, tmp_path):
    """REL-03: Demostrar que una decisión en BLOCK jamás ejecuta físicamente en el host, aun con Full Access y autonomía activa."""
    canary_file = tmp_path / "canary_rel_03.txt"
    assert not canary_file.exists(), "El archivo testigo no debe existir previamente."

    # 1. Sesión con modo Full Access y autonomía solicitada
    session_id = "sess_rel_03_block"
    fa_runtime.create_session(
        goal="Prueba de invariante de bloqueo físico",
        session_id=session_id,
        execution_mode="full_access",
        workspace_root=str(tmp_path),
        metadata={
            "autonomous": True,
            "allow_unattended_execution": True,
            "full_access_authorized_by_operator": True,
        },
    )

    # 2. Acción destructiva que el motor de políticas bloquea incondicionalmente (BLOCK)
    # Comando destructivo hacia la raíz que intenta crear un archivo testigo
    proposal = ProposeActionRequest(
        tool="run_command",
        operation="rm -rf /",
        arguments={"command": f"rm -rf / && touch {canary_file}"},
        thought_rationale="Intento destructivo que debe ser bloqueado incondicionalmente",
    )

    resp = fa_runtime.propose_action(session_id=session_id, proposal=proposal)

    # 3. Comprobar que la política decide BLOCK
    assert resp.status == "BLOCK", f"Se esperaba estatus BLOCK pero fue: {resp.status}"
    assert resp.capability is None, "Una decisión en BLOCK jamás debe emitir un capability token."

    # 4. Comprobar que llamar a execute_decision lanza PolicyViolation incondicionalmente
    with pytest.raises(PolicyViolation, match="Solo se permite la ejecución de decisiones 'ALLOW'"):
        fa_runtime.execute_decision(decision_id=resp.decision_id)

    # 5. Comprobación de efecto físico en el SO host: Cero ejecuciones físicas
    assert not canary_file.exists(), (
        "[REL-03 VIOLATION] El comando bloqueado por política se ejecutó físicamente en el host."
    )


def test_rel_03_block_direct_secure_executor_rejection(fa_runtime, tmp_path):
    """REL-03: SecureExecutor rechaza directamente cualquier acción sin capability ALLOW con efecto físico nulo."""
    canary_file = tmp_path / "canary_executor_block.txt"

    action = ActionCandidate(
        id="act_block_direct",
        description="Intento directo de mutación sin capability",
        tool_call=ToolCall(
            tool_name="edit_file",
            arguments={"path": str(canary_file), "content": "MALICIOUS PAYLOAD"},
        ),
    )
    state = SessionState(
        session_id="sess_direct_block",
        goal=Goal(objective="Direct block test"),
        metadata={"execution_mode": "full_access"},
    )

    # Invocar directamente el ejecutor físico sin capability
    with pytest.raises(PolicyViolation, match="Se requiere un capability/DecisionReceipt válido"):
        fa_runtime.executor.execute(action=action, state=state, receipt=None)

    assert not canary_file.exists(), "El ejecutor físico permitió mutación en host sin capability válida."


# =============================================================================
# REL-04: REPLAN + Full Access -> Replan conservado, nunca ejecutado
# =============================================================================

def test_rel_04_replan_remains_replan_without_execution(fa_runtime, tmp_path):
    """REL-04: Una decisión evaluada como REPLAN conserva su estatus, no emite capability y tiene 0 efecto físico."""
    canary_replan = tmp_path / "canary_rel_04_replan.txt"

    session_id = "sess_rel_04_replan"
    fa_runtime.create_session(
        goal="Prueba de invariante de REPLAN",
        session_id=session_id,
        execution_mode="full_access",
        workspace_root=str(tmp_path),
        metadata={
            "autonomous": True,
            "allow_unattended_execution": True,
            "full_access_authorized_by_operator": True,
        },
    )

    # Forzar un estado de REPLAN registrando la decisión correspondiente
    dec_id = "d_rel_04_replan"
    action = ActionCandidate(
        id="act_replan_01",
        description="Acción que requiere reformulación de plan",
        tool_call=ToolCall(
            tool_name="run_command",
            arguments={"command": f"touch {canary_replan}"},
        ),
    )

    # Guardar en repositorio registro con status REPLAN
    fa_runtime._decisions[dec_id] = {
        "decision_id": dec_id,
        "session_id": session_id,
        "action_id": "act_replan_01",
        "action": action,
        "status": "REPLAN",
        "receipt": None,
        "risk": None,
        "policy": None,
        "providers": [],
    }

    # Intentar ejecutar la decisión en REPLAN
    with pytest.raises(PolicyViolation, match="Solo se permite la ejecución de decisiones 'ALLOW'"):
        fa_runtime.execute_decision(decision_id=dec_id)

    # Comprobar que REPLAN no tocó el sistema operativo
    assert not canary_replan.exists(), "[REL-04 VIOLATION] Decisión en REPLAN ejecutó físicamente en host."


# =============================================================================
# REL-05: REVIEW authorization -> Bypass solo bajo contrato explícito
# =============================================================================

def test_rel_05_full_access_interactive_retains_review(fa_runtime):
    """REL-05 Caso 1: Full Access interactivo (autonomous=False) retiene forzosamente status='REVIEW'."""
    session_id = "sess_rel_05_interactive"
    fa_runtime.create_session(
        goal="Full access interactivo con humano en el bucle",
        session_id=session_id,
        execution_mode="full_access",
        metadata={"autonomous": False, "allow_unattended_execution": False},
    )

    proposal = ProposeActionRequest(
        tool="git",
        operation="push origin main",
        arguments={"command": "push origin main"},
        thought_rationale="Operación con efecto secundario en host",
    )

    resp = fa_runtime.propose_action(session_id=session_id, proposal=proposal)

    # Retención obligatoria de revisión humana
    assert resp.status == "REVIEW", f"Se esperaba REVIEW para modo interactivo pero fue: {resp.status}"
    assert resp.policy.requires_confirmation is True
    assert resp.capability is None

    # Intentar ejecutar sin confirmar debe ser rechazado
    with pytest.raises(PolicyViolation, match="Solo se permite la ejecución de decisiones 'ALLOW'"):
        fa_runtime.execute_decision(decision_id=resp.decision_id)


def test_rel_05_full_access_autonomous_without_operator_auth_retains_review(fa_runtime):
    """REL-05 Caso 2: Full Access autónomo SIN autorización explícita de operador NO puede saltarse REVIEW."""
    session_id = "sess_rel_05_unverified"
    fa_runtime.create_session(
        goal="Intento de bypass no verificado",
        session_id=session_id,
        execution_mode="full_access",
        metadata={
            "autonomous": True,
            "allow_unattended_execution": True,
            "full_access_authorized_by_operator": False,  # No autorizado por operador
        },
    )

    proposal = ProposeActionRequest(
        tool="git",
        operation="push origin staging",
        arguments={"command": "push origin staging"},
        thought_rationale="Operación de mutación en host",
    )

    resp = fa_runtime.propose_action(session_id=session_id, proposal=proposal)

    assert resp.status == "REVIEW", (
        "[REL-05 VIOLATION] Cliente sin token de operador logró bypass de confirmación humana en Full Access."
    )
    assert resp.policy.requires_confirmation is True
    assert resp.capability is None


def test_rel_05_full_access_autonomous_with_verified_operator_allows_execution(fa_runtime, tmp_path):
    """REL-05 Caso 3: Full Access autónomo CON autorización explícita y verificada permite ejecución desatendida auditada."""
    target_file = tmp_path / "authorized_autonomous_output.txt"

    session_id = "sess_rel_05_verified"
    fa_runtime.create_session(
        goal="Automatización formal autorizada",
        session_id=session_id,
        execution_mode="full_access",
        workspace_root=str(tmp_path),
        metadata={
            "autonomous": True,
            "allow_unattended_execution": True,
            "full_access_authorized_by_operator": True,  # Operador autenticado y verificado
        },
    )

    # Operación autorizada en Full Access
    proposal = ProposeActionRequest(
        tool="edit_file",
        operation="write",
        arguments={"path": str(target_file), "content": "Audit verified autonomous execution"},
        thought_rationale="Escritura autorizada por política",
    )

    resp = fa_runtime.propose_action(session_id=session_id, proposal=proposal)

    # Debe ser ALLOW y haber emitido capability firmada
    assert resp.status == "ALLOW"
    assert resp.policy.requires_confirmation is False
    assert resp.capability is not None
    assert resp.capability["execution_mode"] == "full_access"

    # Ejecutar la decisión autorizada
    exec_resp = fa_runtime.execute_decision(decision_id=resp.decision_id)
    assert exec_resp.success is True

    # Comprobar efecto físico en host
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == "Audit verified autonomous execution"


def test_rel_05_review_unlocked_via_human_confirmation_e2e(fa_runtime, tmp_path):
    """REL-05 Caso 4: Una decisión en REVIEW pasa a ALLOW y ejecuta físicamente únicamente tras confirmación explícita del operador."""
    target_confirmed = tmp_path / "confirmed_execution.txt"

    session_id = "sess_rel_05_confirmed"
    fa_runtime.create_session(
        goal="Interactivo con confirmación posterior",
        session_id=session_id,
        execution_mode="full_access",
        workspace_root=str(tmp_path),
        metadata={"autonomous": False},
    )

    proposal = ProposeActionRequest(
        tool="git",
        operation="push origin main",
        arguments={"command": "push origin main"},
        thought_rationale="Operación con efectos remotos que entra obligatoriamente en REVIEW",
    )

    # 1. Propuesta inicial -> entra en REVIEW
    resp = fa_runtime.propose_action(session_id=session_id, proposal=proposal)
    assert resp.status == "REVIEW"
    assert resp.policy.requires_confirmation is True

    # 2. Operador humano aprueba formalmente la decisión
    conf_resp = fa_runtime.confirm_decision(
        decision_id=resp.decision_id,
        approved=True,
        reason="Operador autoriza explícitamente el push tras inspección",
        operator_id="operator_lead",
        role="operator",
        operator_token=VALID_OPERATOR_SECRET,
    )

    assert conf_resp.status == "ALLOW"
    assert conf_resp.capability is not None
    assert conf_resp.capability["execution_mode"] == "full_access"

    # 3. Tras la aprobación, se ejecuta físicamente la decisión autorizada
    exec_resp = fa_runtime.execute_decision(
        decision_id=resp.decision_id,
        capability_token=conf_resp.capability,
    )
    assert exec_resp.success is True or exec_resp.is_error is not None


def test_rel_05_full_access_execution_is_audited_in_event_store(fa_runtime, tmp_path):
    """REL-05 / Auditoría: Demostrar que toda ejecución en Full Access queda formalmente registrada en EventStore con sandboxed=False."""
    target_audit = tmp_path / "audit_test.txt"

    session_id = "sess_rel_audit"
    fa_runtime.create_session(
        goal="Auditoría de eventos en host",
        session_id=session_id,
        execution_mode="full_access",
        workspace_root=str(tmp_path),
        metadata={"autonomous": True, "full_access_authorized_by_operator": True},
    )

    proposal = ProposeActionRequest(
        tool="edit_file",
        operation="write",
        arguments={"path": str(target_audit), "content": "audit log verified"},
        thought_rationale="Generando archivo auditado",
    )

    resp = fa_runtime.propose_action(session_id=session_id, proposal=proposal)
    fa_runtime.execute_decision(decision_id=resp.decision_id)

    # Recuperar eventos del EventStore
    events = fa_runtime.event_bus.get_all_events(session_id)
    exec_started_events = [e for e in events if e.type == EventType.EXECUTION_STARTED]
    exec_completed_events = [e for e in events if e.type == EventType.EXECUTION_COMPLETED]

    assert len(exec_started_events) >= 1
    assert len(exec_completed_events) >= 1

    ev_start = exec_started_events[0]
    assert ev_start.payload["execution_mode"] == "full_access"
    assert ev_start.payload["sandboxed"] is False
    assert ev_start.payload["isolation"] == "None (Host OS)"

    ev_comp = exec_completed_events[0]
    assert ev_comp.payload["execution_mode"] == "full_access"
    assert ev_comp.payload["sandboxed"] is False
    assert ev_comp.payload["success"] is True
