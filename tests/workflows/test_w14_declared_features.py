"""W-14: input/output_mapping, merge_policy='reduce', default_edge y aprobador registrado."""

from praxeon.workflows.engine import WorkflowEngine
from praxeon.workflows.models import (
    NodeStatus, NodeType, WorkflowDefinition, WorkflowEdge, WorkflowNode, WorkflowStatus,
)


def _linear(task_kwargs, handler, variables=None):
    nodes = {
        "s": WorkflowNode(node_id="s", name="s", node_type=NodeType.START),
        "t": WorkflowNode(node_id="t", name="t", node_type=NodeType.TASK, tool_name="h", **task_kwargs),
        "e": WorkflowNode(node_id="e", name="e", node_type=NodeType.END),
    }
    edges = [WorkflowEdge(edge_id="1", from_node="s", to_node="t"), WorkflowEdge(edge_id="2", from_node="t", to_node="e")]
    wf = WorkflowDefinition(workflow_id="m", name="m", nodes=nodes, edges=edges, variables=variables or {})
    return WorkflowEngine(wf, task_handlers={"h": handler})


def test_input_and_output_mapping():
    seen = {}

    def handler(inputs):
        seen.update(inputs)
        return {"result": {"value": inputs["objetivo"] * 2}}

    eng = _linear(
        {"input_mapping": {"objetivo": "variables.base"}, "output_mapping": {"resultado_final": "result.value"}},
        handler, {"base": 21},
    )
    ctx = eng.run_to_completion(concurrent=False)
    assert ctx.status == WorkflowStatus.COMPLETED
    assert seen["objetivo"] == 21
    assert ctx.variables["resultado_final"] == 42


def test_parallel_join_reduce_merges_deeply():
    nodes = {
        "s": WorkflowNode(node_id="s", name="s", node_type=NodeType.START),
        "f": WorkflowNode(node_id="f", name="f", node_type=NodeType.PARALLEL_FORK),
        "a": WorkflowNode(node_id="a", name="a", node_type=NodeType.TASK, tool_name="a"),
        "b": WorkflowNode(node_id="b", name="b", node_type=NodeType.TASK, tool_name="b"),
        "j": WorkflowNode(node_id="j", name="j", node_type=NodeType.PARALLEL_JOIN,
                          control_config={"join_policy": "all", "merge_policy": "reduce"}),
        "e": WorkflowNode(node_id="e", name="e", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="1", from_node="s", to_node="f"),
        WorkflowEdge(edge_id="2", from_node="f", to_node="a"), WorkflowEdge(edge_id="3", from_node="f", to_node="b"),
        WorkflowEdge(edge_id="4", from_node="a", to_node="j"), WorkflowEdge(edge_id="5", from_node="b", to_node="j"),
        WorkflowEdge(edge_id="6", from_node="j", to_node="e"),
    ]
    wf = WorkflowDefinition(workflow_id="r", name="r", nodes=nodes, edges=edges)
    eng = WorkflowEngine(wf, task_handlers={
        "a": lambda _i: {"cfg": {"x": 1}, "items": [1]},
        "b": lambda _i: {"cfg": {"y": 2}, "items": [2]},
    })
    ctx = eng.run_to_completion(concurrent=False)
    merged = ctx.node_outputs["j"]
    assert merged["cfg"] == {"x": 1, "y": 2}
    assert sorted(merged["items"]) == [1, 2]


def test_decision_default_edge_is_used_when_condition_fails():
    nodes = {
        "s": WorkflowNode(node_id="s", name="s", node_type=NodeType.START),
        "d": WorkflowNode(node_id="d", name="d", node_type=NodeType.DECISION, control_config={
            "condition": {"field": "variables.ok", "operator": "is_true"}, "default_edge": "fallback"}),
        "e": WorkflowNode(node_id="e", name="e", node_type=NodeType.END),
    }
    edges = [WorkflowEdge(edge_id="1", from_node="s", to_node="d"),
             WorkflowEdge(edge_id="fallback", from_node="d", to_node="e")]
    eng = WorkflowEngine(WorkflowDefinition(workflow_id="d", name="d", nodes=nodes, edges=edges, variables={"ok": False}))
    ctx = eng.run_to_completion(concurrent=False)
    assert ctx.node_outputs["d"]["branch_taken"] == "fallback"
    assert ctx.status == WorkflowStatus.COMPLETED


def test_approver_is_recorded_on_human_approval():
    nodes = {
        "s": WorkflowNode(node_id="s", name="s", node_type=NodeType.START),
        "g": WorkflowNode(node_id="g", name="g", node_type=NodeType.HUMAN_APPROVAL),
        "e": WorkflowNode(node_id="e", name="e", node_type=NodeType.END),
    }
    edges = [WorkflowEdge(edge_id="1", from_node="s", to_node="g"), WorkflowEdge(edge_id="2", from_node="g", to_node="e")]
    eng = WorkflowEngine(WorkflowDefinition(workflow_id="h", name="h", nodes=nodes, edges=edges))
    eng.run_to_completion(concurrent=False)
    assert eng.context.node_states["g"] == NodeStatus.WAITING_APPROVAL
    assert eng.approve_node("g", True, "ok", approver_id="adrian") is True
    assert eng.context.node_outputs["g"]["approved_by"] == "adrian"
