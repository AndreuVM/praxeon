"""Pruebas unitarias para Gobierno, Aislamiento de Capacidades y Contención de Privilegios (Fase 5 - F5-03).

Valida:
- Detección de integridad adulterada (TAMPERED_INTEGRITY).
- Validación de agentes registrados y estados operativos (ACTIVE vs INACTIVE/TERMINATED).
- Prevención de escalación de privilegios (PRIVILEGE_ESCALATION) cuando un emisor restringido delega herramientas que no posee.
- Validación de herramientas autorizadas para el receptor (FORBIDDEN_TOOL / UNAUTHORIZED_TOOL).
- Verificación de capabilities requeridas (MISSING_CAPABILITY).
- Tolerancia a perfiles de riesgo del receptor (EXCEEDS_RISK_TOLERANCE).
- Integración fluida como middleware interceptor en AgentMessageBus.
"""

import pytest

from praxeon.agents import (
    AgentDefinition,
    AgentGovernanceGuard,
    AgentMessage,
    AgentMessageBus,
    AgentStatus,
    AgentTemplateCatalog,
    GovernanceViolationType,
    MessagePriority,
    MessageType,
    RiskProfile,
    TopologyType,
)
from praxeon.domain.models import RiskLevel


@pytest.fixture
def governance_setup():
    """Configura un entorno estándar de gobernanza con agentes canónicos."""
    guard = AgentGovernanceGuard(supervisor_id="praxeon_supervisor")

    # Developer: allowed_tools = ["*"], forbidden_tools = ["drop_production_db", ...]
    dev = AgentTemplateCatalog.instantiate("developer", "ag_dev_01")
    guard.register_definition(dev)

    # Researcher: allowed_tools = ["search_web", "read_file", ...], forbidden_tools = ["run_command", "write_file", ...]
    researcher = AgentTemplateCatalog.instantiate("researcher", "ag_res_01")
    guard.register_definition(researcher)

    # Auditor: allowed_tools = ["read_file", "grep_search", ...], forbidden_tools = ["write_file", "edit_file", ...]
    auditor = AgentTemplateCatalog.instantiate("security_auditor", "ag_aud_01")
    guard.register_definition(auditor)

    return guard, dev, researcher, auditor


def test_guard_valid_message_allowed(governance_setup):
    """Valida que un mensaje legítimo entre agentes registrados y con herramientas autorizadas sea aprobado."""
    guard, _, _, _ = governance_setup

    msg = AgentMessage(
        message_id="msg_valid",
        sender_id="ag_res_01",
        receiver_id="ag_dev_01",
        session_id="s1",
        payload={"action": "review_literature", "tool": "read_file"},
    )

    decision = guard.validate_message(msg)
    assert decision.allowed is True
    assert decision.violation is None


def test_guard_tampered_integrity(governance_setup):
    """Valida que un mensaje con payload modificado tras el cálculo de integridad sea rechazado."""
    guard, _, _, _ = governance_setup

    msg = AgentMessage(
        message_id="msg_tampered",
        sender_id="ag_res_01",
        receiver_id="ag_dev_01",
        session_id="s1",
        payload={"action": "safe_action"},
    )
    # Fijar el hash canónico original esperado
    msg.metadata["expected_hash"] = msg.integrity_hash

    # Simular adulteración forzando el atributo interno en bypass de Pydantic
    object.__setattr__(msg, "payload", {"action": "malicious_injected_action"})

    decision = guard.validate_message(msg)
    assert decision.allowed is False
    assert decision.violation is not None
    assert decision.violation.violation_type == GovernanceViolationType.TAMPERED_INTEGRITY


def test_guard_unregistered_and_inactive_agents(governance_setup):
    """Valida el bloqueo si el emisor o receptor no están registrados o están inactivos."""
    guard, _, _, _ = governance_setup

    # Emisor desconocido
    msg_unknown_sender = AgentMessage(
        message_id="msg_unk",
        sender_id="ag_phantom",
        receiver_id="ag_dev_01",
        session_id="s1",
        payload={"ping": True},
    )
    dec1 = guard.validate_message(msg_unknown_sender)
    assert dec1.allowed is False
    assert dec1.violation.violation_type == GovernanceViolationType.UNREGISTERED_AGENT

    # Destinatario inactivo
    dormant_agent = AgentDefinition(
        agent_id="ag_dormant",
        name="Dormant Worker",
        role="Worker",
        system_prompt="Sleep",
        status=AgentStatus.INACTIVE,
    )
    guard.register_definition(dormant_agent)

    msg_to_inactive = AgentMessage(
        message_id="msg_inact",
        sender_id="ag_dev_01",
        receiver_id="ag_dormant",
        session_id="s1",
        payload={"task": "wake_up"},
    )
    dec2 = guard.validate_message(msg_to_inactive)
    assert dec2.allowed is False
    assert dec2.violation.violation_type == GovernanceViolationType.INACTIVE_AGENT


def test_guard_anti_privilege_escalation(governance_setup):
    """Valida la regla de No-Escalación: un agente no puede solicitar a otro una herramienta que él tiene prohibida."""
    guard, _, _, _ = governance_setup

    # ag_res_01 tiene explícitamente prohibido 'run_command'
    # Intenta pedírselo a ag_dev_01 (que sí lo tiene permitido)
    escalation_attempt = AgentMessage(
        message_id="msg_escalation",
        sender_id="ag_res_01",
        receiver_id="ag_dev_01",
        session_id="s1",
        payload={"instruction": "execute_shell", "tool": "run_command"},
    )

    decision = guard.validate_message(escalation_attempt)
    assert decision.allowed is False
    assert decision.violation is not None
    assert decision.violation.violation_type == GovernanceViolationType.PRIVILEGE_ESCALATION
    assert "Intento de escalación de privilegios" in decision.reason


def test_guard_forbidden_tool_for_receiver(governance_setup):
    """Valida que no se pueda ordenar una herramienta no autorizada para el receptor."""
    guard, _, _, _ = governance_setup

    # ag_dev_01 solicita a ag_aud_01 ejecutar write_to_file (que auditor no tiene en su lista permitida)
    unauthorized_request = AgentMessage(
        message_id="msg_unauth",
        sender_id="ag_dev_01",
        receiver_id="ag_aud_01",
        session_id="s1",
        payload={"action": "overwrite_config", "tool": "write_to_file"},
    )

    decision = guard.validate_message(unauthorized_request)
    assert decision.allowed is False
    assert decision.violation is not None
    assert decision.violation.violation_type == GovernanceViolationType.FORBIDDEN_TOOL


def test_guard_missing_capabilities(governance_setup):
    """Valida el rechazo si la tarea exige capabilities que el receptor no posee."""
    guard, _, _, _ = governance_setup

    msg_cap = AgentMessage(
        message_id="msg_cap",
        sender_id="ag_dev_01",
        receiver_id="ag_aud_01",
        session_id="s1",
        payload={"task": "deploy_prod", "required_capability": "cloud_infrastructure_write"},
    )

    decision = guard.validate_message(msg_cap)
    assert decision.allowed is False
    assert decision.violation is not None
    assert decision.violation.violation_type == GovernanceViolationType.MISSING_CAPABILITY


def test_guard_risk_tolerance_limit(governance_setup):
    """Valida el bloqueo si el riesgo excede la tolerancia del receptor y requiere confirmación."""
    guard, _, _, _ = governance_setup

    # Registrar agente con bajo perfil de riesgo
    conservative_agent = AgentDefinition(
        agent_id="ag_conservative",
        name="Conservative Worker",
        role="Assistant",
        system_prompt="Safe only",
        risk_profile=RiskProfile(
            max_risk_level=RiskLevel.LOW,
            require_human_confirmation=False,
        ),
    )
    guard.register_definition(conservative_agent)

    msg_risky = AgentMessage(
        message_id="msg_risk",
        sender_id="ag_dev_01",
        receiver_id="ag_conservative",
        session_id="s1",
        payload={"action": "drop_temp_tables", "risk_level": "CRITICAL"},
    )

    decision = guard.validate_message(msg_risky)
    assert decision.allowed is False
    assert decision.violation is not None
    assert decision.violation.violation_type == GovernanceViolationType.EXCEEDS_RISK_TOLERANCE


def test_guard_bus_interceptor_integration(governance_setup):
    """Valida la integración real como middleware en AgentMessageBus."""
    guard, dev, researcher, _ = governance_setup

    bus = AgentMessageBus(default_topology=TopologyType.MESH)
    bus.register_agent(dev.agent_id)
    bus.register_agent(researcher.agent_id)

    # Instalar interceptor de gobernanza en el bus
    bus.add_interceptor(guard.intercept_message)

    # 1. Mensaje malicioso / con escalación de privilegios
    bad_msg = AgentMessage(
        message_id="bad_01",
        sender_id=researcher.agent_id,
        receiver_id=dev.agent_id,
        session_id="s_test",
        payload={"command": "rm -rf /", "tool": "run_command"},
    )
    # send debe retornar False porque el interceptor lo anuló
    assert bus.send(bad_msg) is False
    assert bus.receive(dev.agent_id) is None
    assert len(guard.get_violations()) == 1

    # 2. Mensaje benigno
    good_msg = AgentMessage(
        message_id="good_01",
        sender_id=researcher.agent_id,
        receiver_id=dev.agent_id,
        session_id="s_test",
        payload={"action": "read_doc", "tool": "read_file"},
    )
    assert bus.send(good_msg) is True
    received = bus.receive(dev.agent_id)
    assert received is not None
    assert received.message_id == "good_01"
