"""Pruebas unitarias para el Adaptive Agent Runtime Holístico (Fase 8 - F8-01).

Valida:
- Inicialización de sesión y siembra de fragmentos en ContextManager.
- Ciclo de ejecución nominal de extremo a extremo: Clasificación -> Ruteo -> Despacho -> Supervisión.
- Escalado dinámico en vuelo ante fallos o incertidumbre.
- Bloqueo preventivo y suspensión humana (SUSPEND_HITL) ante intentos no autorizados.
- Consulta de telemetría, historial de pasos y estado de sesión.
"""

import pytest

from praxeon.agents import AgentRegistry, AgentTemplateCatalog
from praxeon.domain.assessment import RiskLevel
from praxeon.routing import RoutingStrategyType
from praxeon.runtime.adaptive import (
    AdaptiveAgentRuntime,
    AdaptiveExecutionSummary,
    AdaptiveSessionState,
    StepDispatchSpec,
    TrajectoryDirective,
)


@pytest.fixture
def runtime():
    """Instancia un AdaptiveAgentRuntime con el catálogo canónico de agentes."""
    registry = AgentRegistry()
    registry.register(AgentTemplateCatalog.instantiate("developer", "ag_dev"))
    registry.register(AgentTemplateCatalog.instantiate("security_auditor", "ag_sec"))
    registry.register(AgentTemplateCatalog.instantiate("researcher", "ag_res"))
    registry.register(AgentTemplateCatalog.instantiate("writer", "ag_writer"))

    return AdaptiveAgentRuntime(registry=registry)


def test_initialize_session(runtime):
    """Valida la creación formal de la sesión y la siembra de la meta en ContextManager."""
    state = runtime.initialize_session(
        session_id="sess_init_01",
        goal="Build secure token validation pipeline",
        primary_agent_id="ag_dev",
    )

    assert state.session_id == "sess_init_01"
    assert state.status == "INITIALIZED"
    assert state.primary_agent_id == "ag_dev"
    assert state.step_count == 0

    # Comprobar que la meta se encuentra en ContextManager
    items = runtime.context_manager.get_items()
    assert any("secure token validation" in item.content.lower() for item in items)


def test_execute_task_nominal_flow(runtime):
    """Valida el ciclo nominal completo de ejecución con clasificación, ruteo y conclusión exitosa."""
    prompt = "Read project configurations and verify clean code standards"

    summary = runtime.execute_task(
        prompt=prompt,
        session_id="sess_nominal_01",
        max_steps=3,
        routing_strategy=RoutingStrategyType.RULE_BASED,
    )

    assert summary.session_id == "sess_nominal_01"
    assert summary.status == "COMPLETED"
    assert summary.total_steps >= 1
    assert summary.total_tokens > 0
    assert summary.total_cost > 0.0
    assert len(summary.agents_involved) >= 1
    assert "TERMINATE" in summary.directives_applied or "CONTINUE" in summary.directives_applied

    # Verificar persistencia de historial de pasos
    history = runtime.get_step_history("sess_nominal_01")
    assert len(history) == summary.total_steps
    assert history[0].policy_decision == "ALLOW"


def test_execute_task_escalation_flow(runtime):
    """Valida que fallos repetidos en ejecución activen el DynamicEscalationEngine en vuelo."""
    prompt = "Fix subtle concurrency deadlock in memory bus"

    # Inyector de pasos simulados que provocan error persistente en los primeros pasos
    def failing_step_executor(spec: StepDispatchSpec, step_idx: int):
        if step_idx < 2:
            return {"tool_name": "view_file", "arguments": {"path": "bus.py"}}, "Traceback error: deadlock detected"
        return {"tool_name": "view_file", "arguments": {"path": "bus.py"}}, "Resolution applied cleanly. task_completed."

    summary = runtime.execute_task(
        prompt=prompt,
        session_id="sess_esc_01",
        max_steps=5,
        custom_step_executor=failing_step_executor,
    )

    assert summary.session_id == "sess_esc_01"
    assert summary.escalations_count >= 1
    assert "ESCALATE" in summary.directives_applied
    assert summary.status == "COMPLETED"


def test_execute_task_security_denial_suspends_hitl(runtime):
    """Valida que un intento de ejecutar una acción no autorizada sea denegado y suspenda la sesión para HITL."""
    prompt = "Clean temporary system directories"

    # Inyector que intenta ejecutar una herramienta destructiva no autorizada
    def unauthorized_step_executor(spec: StepDispatchSpec, step_idx: int):
        return {"tool_name": "rm -rf", "arguments": {"path": "/var/log"}}, "Attempting cleanup"

    summary = runtime.execute_task(
        prompt=prompt,
        session_id="sess_hitl_01",
        max_steps=3,
        custom_step_executor=unauthorized_step_executor,
    )

    assert summary.session_id == "sess_hitl_01"
    assert summary.status == "SUSPENDED_HITL"
    assert "SUSPEND_HITL" in summary.directives_applied
    assert "Suspended for Human Approval" in summary.final_artifact_or_result

    # Verificar que el último paso registrado fue DENY
    history = runtime.get_step_history("sess_hitl_01")
    assert history[-1].policy_decision == "DENY"


def test_runtime_session_queries(runtime):
    """Valida la recuperación consistente de estado y telemetría de sesión."""
    runtime.initialize_session("sess_query_01", "Goal A")
    state = runtime.get_session_state("sess_query_01")
    assert state is not None
    assert state.goal == "Goal A"

    non_existent = runtime.get_session_state("sess_does_not_exist")
    assert non_existent is None
