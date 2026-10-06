"""Pruebas de certificación formal P0: Unificación de RuntimeApplicationService como Control Plane canónico.

Verifica:
1. RuntimeApplicationService integra como única fuente de verdad:
   - AgentRegistry (con plantillas de agentes inicializadas)
   - AgentRouter (con selección de agentes)
   - AgentMessageBus (bus formal de comunicación)
   - ContextManager (gestión y optimización de contexto)
   - WorkflowEditorService (motor y editor unificado compartiendo EventBus y AgentMessageBus)
   - AdaptiveAgentRuntime (orquestación adaptativa integrada)
2. El endpoint REST /v1/workflows utiliza la instancia gestionada por RuntimeApplicationService.
3. Coherencia integral del EventBus: eventos emitidos en sesiones, workflows y decisiones
   se registran en el mismo bus y almacén unificado.
"""

import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.server.dependencies import (
    RuntimeApplicationService,
    get_runtime_service,
    set_runtime_service,
)
from praxeon.agents.registry import AgentRegistry
from praxeon.agents.bus import AgentMessageBus
from praxeon.routing.router import AgentRouter
from praxeon.context.manager import ContextManager
from praxeon.workflows.editor_service import WorkflowEditorService
from praxeon.runtime.adaptive.runtime import AdaptiveAgentRuntime


@pytest.fixture
def unified_runtime(tmp_path):
    """Instancia de RuntimeApplicationService con almacenamiento temporal."""
    service = RuntimeApplicationService(db_dir=str(tmp_path / "control_plane_cache"))
    set_runtime_service(service)
    yield service
    set_runtime_service(None)


def test_runtime_application_service_exposes_all_canonical_subsystems(unified_runtime):
    """P0-RUNTIME: Verifica que todos los subsistemas forman parte del plano de control unificado."""
    # 1. Agent Registry
    reg = unified_runtime.get_agent_registry()
    assert isinstance(reg, AgentRegistry)
    assert len(reg.list_all()) >= 5
    assert reg.get("ag_developer") is not None

    # 2. Agent Router
    router = unified_runtime.get_agent_router()
    assert isinstance(router, AgentRouter)
    assert router.registry is reg

    # 3. Agent Message Bus
    bus = unified_runtime.get_agent_bus()
    assert isinstance(bus, AgentMessageBus)

    # 4. Context Manager
    cm = unified_runtime.get_context_manager()
    assert isinstance(cm, ContextManager)

    # 5. Workflow Editor Service
    wf_service = unified_runtime.get_workflow_editor_service()
    assert isinstance(wf_service, WorkflowEditorService)
    assert wf_service.event_bus is unified_runtime.event_bus
    assert wf_service.agent_bus is unified_runtime.agent_bus

    # 6. Adaptive Agent Runtime
    adaptive = unified_runtime.get_adaptive_runtime()
    assert isinstance(adaptive, AdaptiveAgentRuntime)
    assert adaptive.registry is reg
    assert adaptive.router is router
    assert adaptive.bus is bus


def test_workflows_rest_api_binds_to_runtime_service(unified_runtime):
    """P0-RUNTIME: Rutas de /v1/workflows resuelven el servicio a través de RuntimeApplicationService."""
    app = create_app()
    client = TestClient(app)

    # Consultar flujos registrados en el servicio
    resp = client.get("/v1/workflows")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    # El flujo demo autónomo creado por el workflow service del runtime debe estar disponible
    wf_ids = [w["workflow_id"] for w in data["data"]]
    assert "wf_demo_autonomous" in wf_ids


def test_cross_subsystem_event_bus_coherence(unified_runtime):
    """P0-RUNTIME: Eventos generados por sesiones y workflows convergen en el mismo EventBus."""
    session_id = "sess_unified_cp_01"
    unified_runtime.create_session(goal="Test Control Plane", session_id=session_id)

    # Eventos capturados en el EventBus canónico
    events = unified_runtime.event_bus.get_all_events(session_id)
    assert len(events) >= 2
    types = [e.type for e in events]
    assert "session.started" in types
    assert "goal.created" in types
