"""Pruebas unitarias para el Mecanismo de Escalado Dinámico Supervisado (Fase 7 - F7-04).

Valida:
- Escalado por fallo de tests / loops hacia modelos superiores (MODEL_UPGRADE).
- Escalado por violaciones de seguridad hacia auditores especializados (ROLE_SPECIALIZATION).
- Suspensión formal hacia revisión humana (HITL) al alcanzar el umbral máximo de intentos.
- Despacho coordinado hacia buzones de AgentMessageBus con prioridad alta y contexto de fallo.
- Manejo resiliente cuando no existen agentes alternativos elegibles.
"""

import pytest

from praxeon.agents import (
    AgentMessageBus,
    AgentRegistry,
    AgentTemplateCatalog,
    MessagePriority,
    TopologyType,
)
from praxeon.domain.assessment import RiskLevel
from praxeon.routing import (
    AgentRouter,
    DynamicEscalationEngine,
    EscalationContext,
    EscalationResult,
    EscalationTriggerType,
    RoutingDecision,
    RoutingStrategyType,
    TaskComplexity,
    TaskRequirement,
)


@pytest.fixture
def escalation_setup():
    registry = AgentRegistry()
    registry.register(AgentTemplateCatalog.instantiate("developer", "ag_dev"))
    registry.register(AgentTemplateCatalog.instantiate("security_auditor", "ag_sec"))
    registry.register(AgentTemplateCatalog.instantiate("researcher", "ag_res"))
    registry.register(AgentTemplateCatalog.instantiate("code_reviewer", "ag_rev"))

    router = AgentRouter(registry=registry)
    engine = DynamicEscalationEngine(router=router, max_escalation_level=3)
    bus = AgentMessageBus(default_topology=TopologyType.MESH)
    bus.register_agent("ag_dev")
    bus.register_agent("ag_sec")
    bus.register_agent("ag_res")
    bus.register_agent("ag_rev")

    return router, engine, bus


def test_escalation_by_test_failure_model_upgrade(escalation_setup):
    """Valida que un fallo de tests repetido en un agente ligero escale hacia un modelo Pro de desarrollo."""
    router, engine, _ = escalation_setup

    task = TaskRequirement(
        task_id="t_esc_01",
        prompt="Implement robust numeric matrix parser",
        complexity=TaskComplexity.MEDIUM,
        required_tools=["view_file"],
    )

    # Decisión inicial que asignó al investigador o agente ligero
    initial_decision = RoutingDecision(
        decision_id="rd_init_01",
        task_id=task.task_id,
        selected_agent_id="ag_res",
        confidence=0.70,
        strategy_used=RoutingStrategyType.RULE_BASED,
        escalation_level=0,
        rationale="Asignación inicial",
    )

    context = EscalationContext(
        task=task,
        current_decision=initial_decision,
        trigger_type=EscalationTriggerType.TEST_FAILURE,
        failure_reason="Unit tests failed with IndexError in boundary conditions",
        attempt_count=1,
    )

    result = engine.evaluate_escalation(context)

    assert result.escalated is True
    assert result.requires_human is False
    assert result.escalation_level == 1
    assert result.escalation_type == "MODEL_UPGRADE"
    assert result.new_decision is not None
    assert result.new_decision.selected_agent_id in ["ag_dev", "ag_rev"]
    assert "MODEL_UPGRADE" in result.new_decision.metadata.get("escalation_trigger", "TEST_FAILURE") or "TEST_FAILURE" in result.new_decision.rationale


def test_escalation_by_security_violation(escalation_setup):
    """Valida que la detección de una violación o sospecha de seguridad escale hacia el Security Auditor."""
    router, engine, _ = escalation_setup

    task = TaskRequirement(
        task_id="t_esc_sec",
        prompt="Inspect token generation routine and ensure constant-time comparison",
        required_tools=["view_file"],
    )

    initial_decision = RoutingDecision(
        decision_id="rd_init_sec",
        task_id=task.task_id,
        selected_agent_id="ag_dev",
        confidence=0.75,
        strategy_used=RoutingStrategyType.RULE_BASED,
        escalation_level=0,
        rationale="Desarrollo inicial",
    )

    context = EscalationContext(
        task=task,
        current_decision=initial_decision,
        trigger_type=EscalationTriggerType.SECURITY_VIOLATION,
        failure_reason="Attempted to log raw unmasked authentication tokens to stdout",
        attempt_count=1,
    )

    result = engine.evaluate_escalation(context)

    assert result.escalated is True
    assert result.requires_human is False
    assert result.escalation_type == "ROLE_SPECIALIZATION"
    assert result.new_decision.selected_agent_id == "ag_sec"
    assert "violación de seguridad" in result.rationale.lower()


def test_escalation_exceeding_max_levels_requires_human(escalation_setup):
    """Valida que al superar el umbral de reintentos se detenga el escalado y se requiera operador humano."""
    router, engine, _ = escalation_setup

    task = TaskRequirement(
        task_id="t_esc_hitl",
        prompt="Resolve obscure distributed consensus deadlock",
    )

    # Supongamos que ya estamos en nivel de escalado 3
    initial_decision = RoutingDecision(
        decision_id="rd_hitl_03",
        task_id=task.task_id,
        selected_agent_id="ag_dev",
        confidence=0.85,
        strategy_used=RoutingStrategyType.ADAPTIVE,
        escalation_level=3,
        rationale="Tercer intento",
    )

    context = EscalationContext(
        task=task,
        current_decision=initial_decision,
        trigger_type=EscalationTriggerType.TOOL_LOOP,
        failure_reason="Deadlock persists across 3 refactor cycles; cyclic dependency detected",
        attempt_count=3,
    )

    result = engine.evaluate_escalation(context)

    assert result.escalated is True
    assert result.requires_human is True
    assert result.escalation_type == "HUMAN_INTERVENTION"
    assert result.new_decision is None
    assert "revisión humana obligatoria" in result.rationale


def test_escalate_and_dispatch_to_bus(escalation_setup):
    """Valida la emisión de un mensaje formal con prioridad HIGH hacia el buzón del nuevo agente."""
    router, engine, bus = escalation_setup

    task = TaskRequirement(
        task_id="t_dispatch_esc",
        prompt="Verify parser edge cases",
        required_tools=["view_file"],
    )

    initial_decision = RoutingDecision(
        decision_id="rd_disp_01",
        task_id=task.task_id,
        selected_agent_id="ag_res",
        confidence=0.70,
        strategy_used=RoutingStrategyType.RULE_BASED,
        escalation_level=0,
        rationale="Inicial",
    )

    context = EscalationContext(
        task=task,
        current_decision=initial_decision,
        trigger_type=EscalationTriggerType.TEST_FAILURE,
        failure_reason="Assert error in test_parser",
        attempt_count=1,
    )

    result, msg = engine.escalate_and_dispatch(
        context=context,
        bus=bus,
        session_id="sess_escalation_test",
    )

    assert result.escalated is True
    assert msg is not None
    assert msg.priority == MessagePriority.HIGH
    assert msg.receiver_id == result.new_decision.selected_agent_id
    assert msg.payload["trigger"] == "TEST_FAILURE"
    assert msg.payload["failure_reason"] == context.failure_reason

    # Comprobar recepción en el buzón del destinatario
    received = bus.receive(msg.receiver_id)
    assert received is not None
    assert received.message_id == msg.message_id
    assert received.payload["escalation_level"] == 1
