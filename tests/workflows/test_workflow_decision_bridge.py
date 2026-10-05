"""Pruebas unitarias para la Integración con Decision Tree y EventStore (Fase 6 - F6-03).

Valida:
- Trazabilidad y registro de eventos inmutables en EventStore por cada nodo de workflow.
- Mapeo y acumulación en tiempo real en BranchPath y BranchStep del árbol de razonamiento.
- Gobernanza física preventiva con interceptores de política (PolicyDecision).
- Flujo de solicitud y otorgamiento de aprobación humana (WAITING_APPROVAL -> APPROVAL_COMPLETED).
- Reconstrucción determinista fiel del estado del workflow mediante Event Sourcing Replay.
"""

import pytest

from praxeon.domain.assessment import RiskAssessment, RiskLevel
from praxeon.domain.branch import BranchStatus
from praxeon.domain.decision import DecisionStatus, PolicyDecision
from praxeon.domain.events import EventType
from praxeon.runtime.event_bus import EventBus, EventStore
from praxeon.workflows import (
    NodeStatus,
    NodeType,
    WorkflowDecisionBridge,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowEngine,
    WorkflowNode,
    WorkflowStatus,
)


def create_governed_workflow() -> WorkflowDefinition:
    """Flujo de 3 nodos para validación de gobernanza y eventos."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start Flow", node_type=NodeType.START),
        "compile": WorkflowNode(
            node_id="compile",
            name="Compile Code",
            node_type=NodeType.TASK,
            tool_name="compile_tool",
            inputs={"flags": "-O3"},
        ),
        "end": WorkflowNode(node_id="end", name="End Flow", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="compile"),
        WorkflowEdge(edge_id="e2", from_node="compile", to_node="end"),
    ]
    return WorkflowDefinition(workflow_id="wf_gov_test", name="Governed Flow", nodes=nodes, edges=edges)


def test_bridge_publishes_lifecycle_events_and_branch_steps():
    """Valida la emisión de eventos canónicos y la vinculación a BranchStep en el árbol de razonamiento."""
    event_store = EventStore(db_path=":memory:")
    bus = EventBus(store=event_store)

    wf = create_governed_workflow()
    engine = WorkflowEngine(workflow=wf)
    engine.register_task_handler("compile_tool", lambda inp: {"binary": "a.out"})

    bridge = WorkflowDecisionBridge(engine=engine, event_bus=bus, session_id="sess_gov_01")
    ctx = bridge.run_to_completion()

    assert ctx.status == WorkflowStatus.COMPLETED
    assert bridge.active_branch.status == BranchStatus.SUCCEEDED
    # 3 pasos registrados en el árbol de razonamiento: start, compile, end
    assert len(bridge.active_branch.steps) == 3

    # Verificar eventos persistidos en EventStore
    events = event_store.get_events(session_id="sess_gov_01")
    event_types = [e.type for e in events]

    assert EventType.SESSION_STARTED.value in event_types
    assert EventType.ACTION_PROPOSED.value in event_types
    assert EventType.RISK_ASSESSED.value in event_types
    assert EventType.POLICY_DECIDED.value in event_types
    assert EventType.EXECUTION_STARTED.value in event_types
    assert EventType.EXECUTION_COMPLETED.value in event_types
    assert EventType.OBSERVATION_RECORDED.value in event_types
    assert EventType.SESSION_COMPLETED.value in event_types


def test_bridge_human_approval_pause_and_resume():
    """Valida que un nodo que requiere aprobación se pause en WAITING_APPROVAL y prosiga al aprobarse."""
    event_store = EventStore(db_path=":memory:")
    bus = EventBus(store=event_store)

    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "deploy_prod": WorkflowNode(
            node_id="deploy_prod",
            name="Deploy Production",
            node_type=NodeType.TASK,
            tool_name="deploy_tool",
            metadata={"require_confirmation": True},
        ),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="deploy_prod"),
        WorkflowEdge(edge_id="e2", from_node="deploy_prod", to_node="end"),
    ]
    wf = WorkflowDefinition(workflow_id="wf_approval", name="Approval Flow", nodes=nodes, edges=edges)
    engine = WorkflowEngine(workflow=wf)
    engine.register_task_handler("deploy_tool", lambda inp: {"deployed": True})

    bridge = WorkflowDecisionBridge(engine=engine, event_bus=bus, session_id="sess_appr_01")

    # Ejecutar hasta el freno de aprobación
    bridge.run_to_completion()

    # El flujo debió pausarse en deploy_prod
    assert engine.context.status == WorkflowStatus.PAUSED
    assert engine.context.node_states["deploy_prod"] == NodeStatus.WAITING_APPROVAL

    # Aprobar el nodo
    approved = bridge.approve_node("deploy_prod")
    assert approved is True
    assert engine.context.node_states["deploy_prod"] == NodeStatus.READY

    # Continuar la ejecución
    bridge.run_to_completion()
    assert engine.context.status == WorkflowStatus.COMPLETED
    assert engine.context.node_states["deploy_prod"] == NodeStatus.COMPLETED
    assert engine.context.node_states["end"] == NodeStatus.COMPLETED


def test_bridge_policy_block_and_pruning():
    """Valida que una decisión de política BLOCK detenga la ejecución y pode la rama."""
    event_store = EventStore(db_path=":memory:")
    bus = EventBus(store=event_store)

    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "danger_node": WorkflowNode(
            node_id="danger_node",
            name="Drop DB",
            node_type=NodeType.TASK,
            metadata={"forbidden": True},
        ),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="danger_node"),
        WorkflowEdge(edge_id="e2", from_node="danger_node", to_node="end"),
    ]
    wf = WorkflowDefinition(workflow_id="wf_block", name="Block Flow", nodes=nodes, edges=edges)
    engine = WorkflowEngine(workflow=wf)

    bridge = WorkflowDecisionBridge(engine=engine, event_bus=bus, session_id="sess_block_01")
    ctx = bridge.run_to_completion()

    assert ctx.status == WorkflowStatus.FAILED
    assert ctx.node_states["danger_node"] == NodeStatus.FAILED
    assert "Acción bloqueada por política" in ctx.error_message


def test_bridge_deterministic_replay_from_event_store():
    """Valida la reconstrucción determinista del contexto a partir de los eventos históricos en SQLite."""
    event_store = EventStore(db_path=":memory:")
    bus = EventBus(store=event_store)

    wf = create_governed_workflow()
    engine = WorkflowEngine(workflow=wf)
    engine.register_task_handler("compile_tool", lambda inp: {"binary": "release.bin", "size": 4096})

    session_id = "sess_replay_test"
    bridge = WorkflowDecisionBridge(engine=engine, event_bus=bus, session_id=session_id)
    orig_ctx = bridge.run_to_completion()
    assert orig_ctx.status == WorkflowStatus.COMPLETED

    # Replay determinista sin invocar handlers ni re-ejecutar lógica física
    replayed_ctx = WorkflowDecisionBridge.replay_from_event_store(
        session_id=session_id,
        workflow=wf,
        event_store=event_store,
    )

    assert replayed_ctx.status == WorkflowStatus.COMPLETED
    assert replayed_ctx.node_states["start"] == NodeStatus.COMPLETED
    assert replayed_ctx.node_states["compile"] == NodeStatus.COMPLETED
    assert replayed_ctx.node_outputs["compile"]["binary"] == "release.bin"
    assert replayed_ctx.execution_history == ["start", "compile", "end"]
