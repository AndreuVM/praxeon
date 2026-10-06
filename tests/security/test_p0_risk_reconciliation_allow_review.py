"""Tests de certificación formal P0: Reconciliación de Riesgo y corrección de regresión ALLOW -> REVIEW.

Matriz de casos requerida por la auditoría P0:
1. LOW + allowed -> ALLOW (git status, read_file, pytest, inspección de bajo riesgo)
2. MEDIUM + confirmation -> REVIEW (mutaciones locales en modo restringido)
3. HIGH / CRITICAL + forbidden -> BLOCK (herramientas prohibidas, escalada de privilegios, destructivas)
4. Insufficient evidence -> REPLAN (falta de grounding / evidencias requeridas)
5. Loop / fixation -> REPLAN (probabilidad alta de bucle)
6. Unavailable provider -> Fail closed (ABSTAIN / BLOCK según riesgo, nunca ALLOW ciego)
7. Manual override no debe saltarse policy (BLOCK nunca puede convertirse en ALLOW)
"""

import pytest
from praxeon.domain.assessment import (
    CommandCategory,
    CommandRiskAssessment,
    ProviderAssessment,
    RiskLevel,
)
from praxeon.domain.models import (
    ActionCandidate,
    DecisionStatus,
    Evidence,
    ExecutionMode,
    RiskAssessment,
    ToolCall,
)
from praxeon.runtime.event_bus import EventBus
from praxeon.runtime.state_store import InMemoryStateStore
from praxeon.policy.engine import PolicyEngine
from praxeon.policy.failsafe import FailSafePolicy
from praxeon.policy.permissions import PermissionManager
from praxeon.policy.reconciliation import RiskReconciler
from praxeon.policy.registry import ToolRegistry
from praxeon.reasoning.classifier import CommandClassifier
from praxeon.server.dependencies import RuntimeApplicationService
from praxeon.server.schemas.action import ProposeActionRequest


@pytest.fixture
def policy_engine() -> PolicyEngine:
    registry = ToolRegistry(register_defaults=True)
    failsafe = FailSafePolicy(
        allow_read_only_on_provider_failure=False,
        block_destructive_on_provider_failure=True,
    )
    return PolicyEngine(
        registry=registry,
        failsafe=failsafe,
        permission_manager=PermissionManager(registry=registry),
        classifier=CommandClassifier(),
        reconciler=RiskReconciler(registry=registry),
    )


@pytest.fixture
def runtime_service() -> RuntimeApplicationService:
    service = RuntimeApplicationService(
        state_store=InMemoryStateStore(),
        event_bus=EventBus(),
    )
    return service


# ==============================================================================
# 1. LOW + ALLOWED -> ALLOW (Desacoplamiento genérico y refinamiento de riesgo)
# ==============================================================================

def test_p0_git_status_via_run_command_is_allowed(runtime_service: RuntimeApplicationService):
    """Verifica que 'git status' ejecutado a través de run_command termine en ALLOW (no degradado a REVIEW)."""
    sid = "sess_p0_git_status"
    runtime_service.create_session(goal="Consultar estado del repositorio", session_id=sid, execution_mode="local_restricted")

    proposal = ProposeActionRequest(
        tool="run_command",
        arguments={"command": "git status"},
        thought_rationale="Comprobar árbol de trabajo limpio",
    )
    resp = runtime_service.propose_action(session_id=sid, proposal=proposal)
    assert resp.status == "ALLOW", f"Esperado ALLOW, obtenido {resp.status}. Reasons: {resp.policy.reason_codes}"
    assert resp.policy.requires_confirmation is False
    assert resp.policy.decision == "ALLOW"


def test_p0_read_file_safe_is_allowed(runtime_service: RuntimeApplicationService):
    """Verifica que read_file sobre un archivo existente termine en ALLOW sin pedir confirmación."""
    sid = "sess_p0_read_file"
    runtime_service.create_session(goal="Leer README", session_id=sid, execution_mode="local_restricted")

    proposal = ProposeActionRequest(
        tool="read_file",
        arguments={"path": "README.md"},
        thought_rationale="Inspeccionar documentación",
    )
    resp = runtime_service.propose_action(session_id=sid, proposal=proposal)
    assert resp.status == "ALLOW", f"Esperado ALLOW, obtenido {resp.status}. Reasons: {resp.policy.reason_codes}"
    assert resp.policy.requires_confirmation is False


def test_p0_pytest_via_run_command_is_allowed(runtime_service: RuntimeApplicationService):
    """Verifica que 'pytest' clasificado como BUILD_TEST termine en ALLOW."""
    sid = "sess_p0_pytest"
    runtime_service.create_session(goal="Ejecutar pruebas del proyecto", session_id=sid, execution_mode="local_restricted")

    proposal = ProposeActionRequest(
        tool="run_command",
        arguments={"command": "pytest -v -k test_policy"},
        thought_rationale="Verificar suite de pruebas",
    )
    resp = runtime_service.propose_action(session_id=sid, proposal=proposal)
    assert resp.status == "ALLOW", f"Esperado ALLOW, obtenido {resp.status}. Reasons: {resp.policy.reason_codes}"
    assert resp.policy.requires_confirmation is False


def test_p0_policy_engine_refines_risk_even_when_explicit_risk_assessment_passed(policy_engine: PolicyEngine):
    """Verifica que pasar una RiskAssessment previa (con requires_confirmation=True por la herramienta)
    sea refinada a LOW por RiskReconciler al reconocer la semántica concreta de inspección."""
    action = ActionCandidate(
        id="act_inspect_1",
        description="Inspección git status",
        tool_call=ToolCall(tool_name="run_command", arguments={"command": "git status"}),
    )
    # Simulamos el risk_assessment previo de run_command que requiere confirmación
    explicit_pre_risk = RiskAssessment(
        level=RiskLevel.HIGH,
        requires_confirmation=True,
        executable=True,
        destructive_potential=False,
        reasons=["Herramienta run_command de sistema."],
    )
    provider_ok = ProviderAssessment(provider="TypeSafe", available=True, confidence=0.95, grounded_probability=0.90)

    decision, receipt = policy_engine.evaluate_action(
        action=action,
        state={},
        provider_assessment=provider_ok,
        risk_assessment=explicit_pre_risk,
        execution_mode="local_restricted",
    )

    assert decision.status == DecisionStatus.ALLOW
    assert decision.requires_confirmation is False
    assert decision.risk.level == RiskLevel.LOW
    assert decision.risk.requires_confirmation is False


# ==============================================================================
# 2. MEDIUM + CONFIRMATION -> REVIEW (Mutaciones locales en local_restricted)
# ==============================================================================

def test_p0_write_file_in_restricted_mode_requires_review(runtime_service: RuntimeApplicationService):
    """Verifica que operaciones de mutación de archivos en modo restringido requieran REVIEW / confirmación humana."""
    sid = "sess_p0_write_file"
    runtime_service.create_session(goal="Crear módulo", session_id=sid, execution_mode="local_restricted")

    proposal = ProposeActionRequest(
        tool="write_to_file",
        arguments={"target_file": "scratch/test_out.txt", "code_content": "hello world"},
        thought_rationale="Escribir salida",
    )
    resp = runtime_service.propose_action(session_id=sid, proposal=proposal)
    assert resp.status == "REVIEW"
    assert resp.policy.requires_confirmation is True
    assert resp.policy.decision == "REQUIRE_HUMAN_CONFIRMATION"


# ==============================================================================
# 3. HIGH / CRITICAL + FORBIDDEN -> BLOCK
# ==============================================================================

def test_p0_forbidden_tool_is_blocked(runtime_service: RuntimeApplicationService):
    """Verifica que una herramienta incluida en forbidden_tools sea bloqueada incondicionalmente."""
    sid = "sess_p0_forbidden_tool"
    runtime_service.create_session(goal="Tarea con herramienta prohibida", session_id=sid, execution_mode="local_restricted")
    state = runtime_service.state_store.load_state(sid)
    state.forbidden_tools.add("run_command")
    runtime_service.state_store.save_state(state)

    proposal = ProposeActionRequest(
        tool="run_command",
        arguments={"command": "ls"},
        thought_rationale="Intento de usar herramienta prohibida",
    )
    resp = runtime_service.propose_action(session_id=sid, proposal=proposal)
    assert resp.status == "BLOCK"
    assert "TOOL_FORBIDDEN_BY_SUPERVISOR" in resp.policy.reason_codes
    assert resp.policy.requires_confirmation is False


def test_p0_destructive_command_is_blocked(runtime_service: RuntimeApplicationService):
    """Verifica que comandos shell destructivos (rm -rf) sean bloqueados deterministamente."""
    sid = "sess_p0_destructive"
    runtime_service.create_session(goal="Auditoría destructiva", session_id=sid, execution_mode="full_access")

    proposal = ProposeActionRequest(
        tool="run_command",
        arguments={"command": "rm -rf /var/data"},
        thought_rationale="Limpieza forzada",
    )
    resp = runtime_service.propose_action(session_id=sid, proposal=proposal)
    assert resp.status == "BLOCK"
    assert "DESTRUCTIVE_COMMAND_BLOCK" in resp.policy.reason_codes


def test_p0_privilege_escalation_is_blocked(runtime_service: RuntimeApplicationService):
    """Verifica que comandos de escalada de privilegios (sudo, runas) sean bloqueados deterministamente."""
    sid = "sess_p0_privilege"
    runtime_service.create_session(goal="Acceso privilegiado", session_id=sid, execution_mode="full_access")

    proposal = ProposeActionRequest(
        tool="run_command",
        arguments={"command": "sudo systemctl restart service"},
        thought_rationale="Reinicio con sudo",
    )
    resp = runtime_service.propose_action(session_id=sid, proposal=proposal)
    assert resp.status == "BLOCK"
    assert "PRIVILEGE_ESCALATION_BLOCK" in resp.policy.reason_codes


# ==============================================================================
# 4. INSUFFICIENT EVIDENCE -> REPLAN
# ==============================================================================

def test_p0_missing_required_evidence_replans(policy_engine: PolicyEngine):
    """Verifica que una acción que exige evidencia explícita sea rechazada como REPLAN si la evidencia falta."""
    action = ActionCandidate(
        id="act_needs_ev",
        description="Borrar registro previa confirmación de backup",
        tool_call=ToolCall(tool_name="edit_file", arguments={"path": "config.yaml"}),
        requires_evidence=["backup_completed_claim"],
    )
    decision, _ = policy_engine.evaluate_action(
        action=action,
        state={},
        available_evidence=[],  # Sin la evidencia requerida
    )
    assert decision.status == DecisionStatus.REPLAN
    assert any("MISSING_REQUIRED_EVIDENCE" in r for r in decision.reason_codes)


# ==============================================================================
# 5. LOOP / FIXATION -> REPLAN
# ==============================================================================

def test_p0_high_loop_probability_replans(policy_engine: PolicyEngine):
    """Verifica que alta probabilidad de bucle reportada por el supervisor resulte en REPLAN."""
    action = ActionCandidate(
        id="act_loop",
        description="Paso repetitivo",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "README.md"}),
    )
    provider_loop = ProviderAssessment(
        provider="TypeSafe",
        available=True,
        confidence=0.90,
        loop_probability=0.85,  # >= 0.65 threshold
    )
    decision, _ = policy_engine.evaluate_action(
        action=action,
        state={},
        provider_assessment=provider_loop,
    )
    assert decision.status == DecisionStatus.REPLAN
    assert any("HIGH_LOOP_PROBABILITY" in r for r in decision.reason_codes)


# ==============================================================================
# 6. UNAVAILABLE PROVIDER -> FAIL CLOSED
# ==============================================================================

def test_p0_unavailable_provider_fails_closed_as_abstain(policy_engine: PolicyEngine):
    """Verifica que ante indisponibilidad del proveedor semántico el sistema falle cerrado (ABSTAIN / REVIEW)."""
    action = ActionCandidate(
        id="act_failsafe",
        description="Lectura con proveedor caído",
        tool_call=ToolCall(tool_name="read_file", arguments={"path": "README.md"}),
    )
    provider_down = ProviderAssessment(
        provider="TypeSafe",
        available=False,
        confidence=0.0,
        reason_codes=["PROVIDER_DOWN"],
    )
    decision, _ = policy_engine.evaluate_action(
        action=action,
        state={},
        provider_assessment=provider_down,
    )
    assert decision.status == DecisionStatus.ABSTAIN
    assert "PROVIDER_UNAVAILABLE_FAILSAFE_ABSTAIN" in decision.reason_codes


def test_p0_unavailable_provider_destructive_action_fails_closed_as_block(policy_engine: PolicyEngine):
    """Verifica que una acción destructiva con proveedor caído falle cerrada como BLOCK inmediato."""
    action = ActionCandidate(
        id="act_dest_down",
        description="Eliminar archivo con proveedor caído",
        tool_call=ToolCall(tool_name="delete_file", arguments={"path": "data.db"}),
    )
    provider_down = ProviderAssessment(
        provider="TypeSafe",
        available=False,
        confidence=0.0,
    )
    decision, _ = policy_engine.evaluate_action(
        action=action,
        state={},
        provider_assessment=provider_down,
    )
    assert decision.status == DecisionStatus.BLOCK


# ==============================================================================
# 7. MANUAL OVERRIDE NO DEBE SALTARSE POLICY
# ==============================================================================

def test_p0_manual_override_cannot_approve_blocked_action(runtime_service: RuntimeApplicationService):
    """Verifica que un intento de aprobar manualmente (confirm_decision) una decisión en BLOCK sea rechazado con PermissionError."""
    sid = "sess_p0_manual_override"
    runtime_service.create_session(goal="Prueba de bypass manual", session_id=sid, execution_mode="local_restricted")

    proposal = ProposeActionRequest(
        tool="run_command",
        arguments={"command": "rm -rf /important_project_data"},
        thought_rationale="Comando destructivo",
    )
    resp = runtime_service.propose_action(session_id=sid, proposal=proposal)
    assert resp.status == "BLOCK"
    decision_id = resp.decision_id

    # Intentar autorizar la decisión bloqueada
    with pytest.raises(PermissionError) as exc_info:
        runtime_service.confirm_decision(
            decision_id=decision_id,
            approved=True,
            reason="Operador intentando forzar autorización de un comando destructivo",
            actor="admin_operator",
            role="admin",
        )
    assert "Veto incondicional" in str(exc_info.value)
