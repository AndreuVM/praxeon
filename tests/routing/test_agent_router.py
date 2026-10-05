"""Pruebas unitarias para el Módulo Central AgentRouter (Fase 7 - F7-01).

Valida:
- Filtrado estricto de elegibilidad según capabilities, herramientas prohibidas y nivel de riesgo.
- Enrutamiento manual explícito (MANUAL).
- Enrutamiento determinista basado en reglas de afinidad (RULE_BASED).
- Despacho coordinado hacia buzones de AgentMessageBus.
- Trazabilidad y auditoría de decisiones en el historial.
"""

import pytest

from praxeon.agents import (
    AgentMessageBus,
    AgentRegistry,
    AgentStatus,
    AgentTemplateCatalog,
    TopologyType,
)
from praxeon.domain.assessment import RiskLevel
from praxeon.routing import (
    AgentRouter,
    RoutingStrategyType,
    TaskComplexity,
    TaskRequirement,
)


@pytest.fixture
def populated_router():
    """Instancia un AgentRouter con el catálogo canónico de agentes registrado."""
    registry = AgentRegistry()

    # Registrar Developer, SecurityAuditor, Researcher y Writer
    dev = AgentTemplateCatalog.instantiate("developer", "ag_dev")
    sec = AgentTemplateCatalog.instantiate("security_auditor", "ag_sec")
    res = AgentTemplateCatalog.instantiate("researcher", "ag_res")
    writer = AgentTemplateCatalog.instantiate("writer", "ag_writer")

    registry.register(dev)
    registry.register(sec)
    registry.register(res)
    registry.register(writer)

    return AgentRouter(registry=registry)


def test_router_filter_eligible_agents_by_tools_and_risk(populated_router):
    """Valida que los agentes que tienen prohibidas herramientas o no toleran el riesgo sean excluidos."""
    # Tarea que exige ejecutar comandos shell
    task_shell = TaskRequirement(
        task_id="t_shell_01",
        prompt="Execute unit test runner via command line",
        required_tools=["run_command"],
        inferred_risk=RiskLevel.MEDIUM,
    )

    eligible = populated_router.filter_eligible_agents(task_shell)
    eligible_ids = [a.agent_id for a in eligible]

    # Researcher y Writer tienen prohibido run_command
    assert "ag_dev" in eligible_ids
    assert "ag_res" not in eligible_ids
    assert "ag_writer" not in eligible_ids


def test_router_manual_strategy(populated_router):
    """Valida la asignación manual forzada hacia un agente específico."""
    task = TaskRequirement(
        task_id="t_manual_01",
        prompt="Write release notes",
    )

    decision = populated_router.route(
        task=task,
        strategy=RoutingStrategyType.MANUAL,
        manual_agent_id="ag_writer",
    )

    assert decision.selected_agent_id == "ag_writer"
    assert decision.confidence == 1.0
    assert decision.strategy_used == RoutingStrategyType.MANUAL
    assert "Asignación manual" in decision.rationale


def test_router_rule_based_role_affinity(populated_router):
    """Valida que la estrategia basada en reglas dirija tareas de seguridad al auditor y de código al dev."""
    # 1. Tarea de auditoría de seguridad
    task_sec = TaskRequirement(
        task_id="t_audit_01",
        prompt="Perform static security audit and vulnerability analysis on authentication middleware",
        required_capabilities=["security_audit"],
        complexity=TaskComplexity.HIGH,
    )
    dec_sec = populated_router.route(task_sec, strategy=RoutingStrategyType.RULE_BASED)
    assert dec_sec.selected_agent_id == "ag_sec"
    assert dec_sec.confidence >= 0.7

    # 2. Tarea de desarrollo e implementación
    task_dev = TaskRequirement(
        task_id="t_dev_01",
        prompt="Implement new API endpoint and refactor data models",
        required_capabilities=["code_authoring"],
        required_tools=["write_file"],
        complexity=TaskComplexity.MEDIUM,
    )
    dec_dev = populated_router.route(task_dev, strategy=RoutingStrategyType.RULE_BASED)
    assert dec_dev.selected_agent_id == "ag_dev"
    assert dec_dev.confidence >= 0.7


def test_router_dispatch_to_agent_message_bus(populated_router):
    """Valida que dispatch tome la decisión y deposite el AgentMessage en el buzón del bus."""
    bus = AgentMessageBus(default_topology=TopologyType.MESH)
    bus.register_agent("ag_res")

    task = TaskRequirement(
        task_id="t_research_01",
        prompt="Search literature and find academic benchmarks for reasoning loops",
        required_capabilities=["literature_search"],
    )

    decision, msg = populated_router.dispatch(
        task=task,
        bus=bus,
        session_id="sess_dispatch_01",
    )

    assert decision.selected_agent_id == "ag_res"
    assert msg.receiver_id == "ag_res"
    assert msg.task_id == "t_research_01"

    # Verificar que el mensaje está disponible en el buzón del agente en el bus
    received = bus.receive("ag_res")
    assert received is not None
    assert received.message_id == msg.message_id
    assert received.payload["prompt"] == task.prompt


def test_router_history_tracking(populated_router):
    """Valida el registro ordenado y auditable de decisiones tomadas por el enrutador."""
    t1 = TaskRequirement(task_id="t1", prompt="Audit SQL queries")
    t2 = TaskRequirement(task_id="t2", prompt="Write documentation")

    populated_router.route(t1)
    populated_router.route(t2)

    hist = populated_router.get_history()
    assert len(hist) == 2
    assert hist[0].task_id == "t1"
    assert hist[1].task_id == "t2"
