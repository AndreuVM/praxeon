"""Ejecución de un nodo de workflow como pipeline de pasos: cada paso por separado y su orden (auditoría A-01)."""

from datetime import datetime, timedelta, timezone

import pytest

from praxeon.agents import AgentMessageBus
from praxeon.core.pipeline import Outcome
from praxeon.workflows import (
    NodeStatus,
    NodeType,
    RetryPolicy,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowEngine,
    WorkflowNode,
    WorkflowStatus,
)
from praxeon.workflows import node_execution as N
from praxeon.workflows.supervised import WorkflowApprovalRequired

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def _engine(middle: WorkflowNode, **kw) -> WorkflowEngine:
    """START -> prev (TASK) -> <middle> -> END, con el nodo previo ya completado."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "prev": WorkflowNode(node_id="prev", name="Prev", node_type=NodeType.TASK, tool_name="prev_tool"),
        middle.node_id: middle,
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="prev"),
        WorkflowEdge(edge_id="e2", from_node="prev", to_node=middle.node_id),
        WorkflowEdge(edge_id="e3", from_node=middle.node_id, to_node="end"),
    ]
    engine = WorkflowEngine(workflow=WorkflowDefinition(workflow_id="wf", name="wf", nodes=nodes, edges=edges), **kw)
    engine.context.variables["goal"] = "probar"
    engine.context.node_outputs["prev"] = {"report": "listo", "score": 7}
    engine.context.node_states["prev"] = NodeStatus.COMPLETED
    return engine


def _task(**kw) -> WorkflowNode:
    kw.setdefault("tool_name", "work")
    return WorkflowNode(node_id="mid", name="Mid", node_type=NodeType.TASK, **kw)


def _run(engine, node_id="mid") -> N.NodeRun:
    return N.NodeRun(engine=engine, node_id=node_id, node=engine.workflow.nodes[node_id], now=NOW)


def test_node_steps_run_in_an_explicit_order():
    assert [step.name for step in N.NODE_STEPS] == ["prepare_node", "run_node", "record_node_result"]


def test_every_node_type_has_an_executor():
    assert set(N.NODE_EXECUTORS) == set(NodeType)


def test_prepare_resolves_inputs_and_marks_the_node_running():
    engine = _engine(_task(inputs={"own": 1}, input_mapping={"from_prev": "outputs.prev.score"}))
    run = _run(engine)
    assert N.PrepareNodeStep().run(run) is Outcome.CONTINUE
    assert engine.context.node_states["mid"] == NodeStatus.RUNNING
    inputs = run.resolved_inputs
    assert inputs["goal"] == "probar" and inputs["report"] == "listo" and inputs["upstream_prev"] == {"report": "listo", "score": 7}
    assert inputs["own"] == 1 and inputs["from_prev"] == 7


def test_a_task_runs_its_handler_and_completes():
    engine = _engine(_task(output_mapping={"result": "value"}), task_handlers={"work": lambda inputs: {"value": inputs["score"] * 2}})
    assert N.execute_node(engine, "mid", NOW) is True
    ctx = engine.context
    assert ctx.node_states["mid"] == NodeStatus.COMPLETED and ctx.node_outputs["mid"] == {"value": 14}
    assert ctx.variables["result"] == 14 and ctx.execution_history[-1] == "mid"


def test_a_task_without_handler_fails_closed_unless_simulation_is_allowed():
    engine = _engine(_task())
    assert N.execute_node(engine, "mid", NOW) is False
    assert engine.context.node_states["mid"] == NodeStatus.FAILED and engine.context.status == WorkflowStatus.FAILED
    assert "No execution handler" in engine.context.node_outputs["mid"]["error"]

    simulated = _engine(_task(), allow_synthetic_fallback=True)
    assert N.execute_node(simulated, "mid", NOW) is True
    assert simulated.context.node_outputs["mid"]["simulated"] is True


def test_a_failure_is_retried_with_backoff_before_failing():
    def boom(inputs):
        raise RuntimeError("caído")

    engine = _engine(_task(retry_policy=RetryPolicy(max_retries=2, delay_seconds=10, backoff_multiplier=3)), task_handlers={"work": boom})
    assert N.execute_node(engine, "mid", NOW) is False
    assert engine.context.node_states["mid"] == NodeStatus.RETRYING
    assert engine.context.node_retry_after["mid"] == NOW + timedelta(seconds=10)
    assert N.execute_node(engine, "mid", NOW) is False
    assert engine.context.node_retry_after["mid"] == NOW + timedelta(seconds=30)
    assert N.execute_node(engine, "mid", NOW) is False
    assert engine.context.node_states["mid"] == NodeStatus.FAILED
    assert engine.context.error_message == "Fallo en nodo 'mid': caído"


def test_a_failure_without_delay_is_ready_again_at_once():
    def boom(inputs):
        raise RuntimeError("caído")

    engine = _engine(_task(retry_policy=RetryPolicy(max_retries=1, delay_seconds=0)), task_handlers={"work": boom})
    assert N.execute_node(engine, "mid", NOW) is False
    assert engine.context.node_states["mid"] == NodeStatus.READY


def test_a_tool_that_needs_confirmation_leaves_the_node_waiting():
    def needs_approval(inputs):
        raise WorkflowApprovalRequired("act_1", "git_push", ["REMOTE_MUTATION_REQUIRES_CONFIRMATION"])

    engine = _engine(_task(), task_handlers={"work": needs_approval})
    run = _run(engine)
    N.PrepareNodeStep().run(run)
    assert N.RunNodeStep().run(run) is Outcome.END and run.result is True
    output = engine.context.node_outputs["mid"]
    assert engine.context.node_states["mid"] == NodeStatus.WAITING_APPROVAL
    assert output["awaiting_tool_confirmation"] and output["tool"] == "git_push"


def test_an_agent_node_with_a_bus_waits_for_the_agent():
    bus = AgentMessageBus()
    engine = _engine(WorkflowNode(node_id="mid", name="Mid", node_type=NodeType.AGENT, agent_id="dev"), agent_bus=bus)
    assert N.execute_node(engine, "mid", NOW) is True
    output = engine.context.node_outputs["mid"]
    assert engine.context.node_states["mid"] == NodeStatus.WAITING_RESULT
    assert output["agent_id"] == "dev" and output["status"] == "waiting_result" and output["message_id"].startswith("wf_agent_mid_")


def test_a_human_approval_node_waits_for_a_person():
    node = WorkflowNode(node_id="mid", name="Mid", node_type=NodeType.HUMAN_APPROVAL, inputs={"prompt": "¿Publicar?"})
    engine = _engine(node)
    assert N.execute_node(engine, "mid", NOW) is True
    assert engine.context.node_states["mid"] == NodeStatus.WAITING_APPROVAL
    assert engine.context.node_outputs["mid"]["prompt"] == "¿Publicar?"


def test_a_result_that_arrives_after_cancelling_is_discarded():
    engine = _engine(_task(), task_handlers={"work": lambda inputs: {"value": 1}})
    run = _run(engine)
    N.PrepareNodeStep().run(run)
    N.RunNodeStep().run(run)
    engine.context.status = WorkflowStatus.CANCELLED
    assert N.RecordNodeResultStep().run(run) is Outcome.END and run.result is False
    assert "mid" not in engine.context.node_outputs


@pytest.mark.parametrize("policy, expected", [
    ("shallow", {"parallel_sync": True, "status": "completed", "report": "listo", "score": 7}),
    ("namespace", {"parallel_sync": True, "status": "completed", "prev": {"report": "listo", "score": 7}}),
])
def test_parallel_join_merges_its_branches(policy, expected):
    node = WorkflowNode(node_id="mid", name="Mid", node_type=NodeType.PARALLEL_JOIN, control_config={"merge_policy": policy})
    engine = _engine(node)
    assert N.execute_node(engine, "mid", NOW) is True
    assert engine.context.node_outputs["mid"] == expected


def test_a_delegate_node_without_an_agent_fails():
    node = WorkflowNode(node_id="mid", name="Mid", node_type=NodeType.DELEGATE)
    engine = _engine(node)
    assert N.execute_node(engine, "mid", NOW) is False
    assert "no tiene agente asignado" in engine.context.node_outputs["mid"]["error"]


def test_the_engine_method_delegates_to_the_pipeline():
    engine = _engine(_task(), task_handlers={"work": lambda inputs: {"value": 1}})
    assert engine.execute_node("mid", NOW) is True and engine.context.node_states["mid"] == NodeStatus.COMPLETED
