"""Pruebas unitarias para el filtro estricto de elegibilidad en selección manual de AgentRouter (P1-ROUTING).

Valida:
- Rechazo estricto con IneligibleAgentRoutingError si el agente manual no es elegible.
- Rechazo por herramientas prohibidas.
- Rechazo por capacidades obligatorias ausentes.
- Rechazo por perfil de riesgo incompatible.
- Rechazo por agente inactivo o inexistente.
- Aceptación exitosa cuando el agente satisface todos los requisitos.
- Compatibilidad hacia atrás (IneligibleAgentRoutingError hereda de ValueError).
"""

import pytest

from praxeon.agents import (
    AgentDefinition,
    AgentRegistry,
    AgentStatus,
    AgentTemplateCatalog,
    RiskProfile,
)
from praxeon.domain.assessment import RiskLevel
from praxeon.routing import (
    AgentRouter,
    IneligibleAgentRoutingError,
    RoutingStrategyType,
    TaskComplexity,
    TaskRequirement,
)


@pytest.fixture
def populated_router():
    """Instancia un AgentRouter con el catálogo canónico de agentes registrado."""
    registry = AgentRegistry()

    dev = AgentTemplateCatalog.instantiate("developer", "ag_dev")
    sec = AgentTemplateCatalog.instantiate("security_auditor", "ag_sec")
    res = AgentTemplateCatalog.instantiate("researcher", "ag_res")
    writer = AgentTemplateCatalog.instantiate("writer", "ag_writer")

    registry.register(dev)
    registry.register(sec)
    registry.register(res)
    registry.register(writer)

    return AgentRouter(registry=registry)


def test_manual_routing_success_when_eligible(populated_router):
    """Valida que la asignación manual proceda con éxito cuando el agente satisface todos los criterios."""
    task = TaskRequirement(
        task_id="t_doc_01",
        prompt="Write technical documentation and architecture guides",
        required_capabilities=["technical_writing"],
        inferred_risk=RiskLevel.LOW,
    )

    decision = populated_router.route(
        task=task,
        strategy=RoutingStrategyType.MANUAL,
        manual_agent_id="ag_writer",
    )

    assert decision.selected_agent_id == "ag_writer"
    assert decision.confidence == 1.0
    assert decision.strategy_used == RoutingStrategyType.MANUAL


def test_manual_routing_fails_on_missing_required_capabilities(populated_router):
    """Valida el rechazo si el agente manual carece de capabilities requeridas por la tarea."""
    task = TaskRequirement(
        task_id="t_audit_01",
        prompt="Audit smart contract and verify cryptographic signatures",
        required_capabilities=["security_audit", "vulnerability_remediation"],
        inferred_risk=RiskLevel.LOW,
    )

    # ag_writer no tiene security_audit
    with pytest.raises(IneligibleAgentRoutingError) as exc_info:
        populated_router.route(
            task=task,
            strategy=RoutingStrategyType.MANUAL,
            manual_agent_id="ag_writer",
        )

    err = exc_info.value
    assert err.agent_id == "ag_writer"
    assert err.task_id == "t_audit_01"
    assert "Carece de capabilities obligatorias" in err.reason
    assert "security_audit" in err.details["missing_capabilities"]
    assert isinstance(err, ValueError)  # Compatibilidad con catch ValueError


def test_manual_routing_fails_on_prohibited_tools(populated_router):
    """Valida el rechazo si el agente manual tiene prohibida una herramienta que la tarea exige."""
    task = TaskRequirement(
        task_id="t_shell_01",
        prompt="Run integration test suite via bash",
        required_tools=["run_command"],
        inferred_risk=RiskLevel.MEDIUM,
    )

    # ag_res tiene prohibido run_command
    with pytest.raises(IneligibleAgentRoutingError) as exc_info:
        populated_router.route(
            task=task,
            strategy=RoutingStrategyType.MANUAL,
            manual_agent_id="ag_res",
        )

    err = exc_info.value
    assert err.agent_id == "ag_res"
    assert "Herramientas prohibidas requeridas" in err.reason
    assert "run_command" in err.details["prohibited_tools"]


def test_manual_routing_fails_on_risk_exceeding_tolerance(populated_router):
    """Valida el rechazo si el riesgo inferido de la tarea supera la tolerancia del agente sin HITL."""
    task = TaskRequirement(
        task_id="t_critical_sec_01",
        prompt="Delete production database replica and destroy secrets",
        inferred_risk=RiskLevel.CRITICAL,
    )

    # ag_dev tiene tolerancia máxima HIGH (no CRITICAL)
    with pytest.raises(IneligibleAgentRoutingError) as exc_info:
        populated_router.route(
            task=task,
            strategy=RoutingStrategyType.MANUAL,
            manual_agent_id="ag_dev",
        )

    err = exc_info.value
    assert err.agent_id == "ag_dev"
    assert "supera tolerancia" in err.reason


def test_manual_routing_fails_on_inactive_agent(populated_router):
    """Valida el rechazo si el agente manual está inactivo o deshabilitado."""
    # Desactivar ag_writer
    populated_router.registry.deactivate("ag_writer")

    task = TaskRequirement(
        task_id="t_task_01",
        prompt="Simple draft task",
    )

    with pytest.raises(IneligibleAgentRoutingError) as exc_info:
        populated_router.route(
            task=task,
            strategy=RoutingStrategyType.MANUAL,
            manual_agent_id="ag_writer",
        )

    err = exc_info.value
    assert err.agent_id == "ag_writer"
    assert "no se encuentra en estado ACTIVE" in err.reason
    assert err.details["status"] == "INACTIVE"


def test_manual_routing_fails_on_non_existent_agent(populated_router):
    """Valida el rechazo si el manual_agent_id no existe en el registro."""
    task = TaskRequirement(
        task_id="t_task_ghost",
        prompt="Any task",
    )

    with pytest.raises(IneligibleAgentRoutingError) as exc_info:
        populated_router.route(
            task=task,
            strategy=RoutingStrategyType.MANUAL,
            manual_agent_id="agent_ghost_999",
        )

    err = exc_info.value
    assert err.agent_id == "agent_ghost_999"
    assert "no encontrado en el registro" in err.reason
