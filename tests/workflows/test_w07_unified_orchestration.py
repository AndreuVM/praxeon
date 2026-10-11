"""W-04 / W-07: orquestación unificada (scheduler + REST) y robustez del motor."""

import threading
import time

from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service
from praxeon.workflows.engine import WorkflowEngine
from praxeon.workflows.models import (
    NodeType, WorkflowDefinition, WorkflowEdge, WorkflowNode, WorkflowStatus,
)


def _client(tmp_path, monkeypatch):
    for k in ("PRAXEON_PROFILE", "PRAXEON_ENV", "PRAXEON_API_KEY", "PRAXEON_REQUIRE_AUTH"):
        monkeypatch.delenv(k, raising=False)
    set_runtime_service(RuntimeApplicationService(db_dir=str(tmp_path / "db")))
    return TestClient(create_app(profile="dev"))


def _wait(client, eid, states, timeout=10.0):
    end = time.time() + timeout
    data = {}
    while time.time() < end:
        data = client.get(f"/v1/workflows/executions/{eid}").json()["data"]
        if data["status"] in states:
            return data
        time.sleep(0.2)
    return data


def _single_node_workflow(client, node_payload):
    wf = client.post("/v1/workflows", json={"name": "e2e"}).json()["data"]
    wid = wf["workflow_id"]
    nid = client.post(f"/v1/workflows/{wid}/nodes", json=node_payload).json()["data"]["node_id"]
    client.delete(f"/v1/workflows/{wid}/edges/e_start_end")
    client.post(f"/v1/workflows/{wid}/edges", json={"from_node": "start", "to_node": nid})
    client.post(f"/v1/workflows/{wid}/edges", json={"from_node": nid, "to_node": "end"})
    return wid, nid


def test_execution_endpoint_runs_under_scheduler(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    wid, nid = _single_node_workflow(client, {
        "name": "t", "node_type": "TASK", "tool_name": "run_command",
        "inputs": {"command": "echo desde-scheduler"},
    })
    eid = client.post(f"/v1/workflows/{wid}/executions").json()["data"]["execution_id"]
    data = _wait(client, eid, {"COMPLETED", "FAILED"})
    assert data["status"] == "COMPLETED"
    assert data["node_outputs"][nid]["supervised"] is True


def test_cancel_execution_endpoint(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    wid, nid = _single_node_workflow(client, {"name": "gate", "node_type": "HUMAN_APPROVAL"})
    eid = client.post(f"/v1/workflows/{wid}/executions").json()["data"]["execution_id"]
    data = _wait(client, eid, set(), timeout=1.5)
    assert data["node_states"][nid] == "WAITING_APPROVAL"
    assert client.post(f"/v1/workflows/executions/{eid}/cancel").json()["data"]["status"] == "CANCELLED"


def test_global_timeout_is_enforced_by_engine():
    nodes = {
        "s": WorkflowNode(node_id="s", name="s", node_type=NodeType.START),
        "g": WorkflowNode(node_id="g", name="g", node_type=NodeType.HUMAN_APPROVAL),
        "e": WorkflowNode(node_id="e", name="e", node_type=NodeType.END),
    }
    edges = [WorkflowEdge(edge_id="1", from_node="s", to_node="g"), WorkflowEdge(edge_id="2", from_node="g", to_node="e")]
    wf = WorkflowDefinition(workflow_id="t", name="t", nodes=nodes, edges=edges, timeout_seconds=0.01)
    eng = WorkflowEngine(wf)
    eng.run_to_completion(concurrent=False)
    time.sleep(0.05)
    eng.tick()
    assert eng.context.status == WorkflowStatus.FAILED


def test_cancel_during_execution_discards_result():
    nodes = {
        "s": WorkflowNode(node_id="s", name="s", node_type=NodeType.START),
        "t": WorkflowNode(node_id="t", name="t", node_type=NodeType.TASK, tool_name="slow"),
        "e": WorkflowNode(node_id="e", name="e", node_type=NodeType.END),
    }
    edges = [WorkflowEdge(edge_id="1", from_node="s", to_node="t"), WorkflowEdge(edge_id="2", from_node="t", to_node="e")]
    started, release = threading.Event(), threading.Event()

    def slow(_):
        started.set()
        release.wait(2)
        return {"ok": True}

    eng = WorkflowEngine(WorkflowDefinition(workflow_id="c", name="c", nodes=nodes, edges=edges), task_handlers={"slow": slow})
    eng.step()
    th = threading.Thread(target=eng.step)
    th.start()
    started.wait(2)
    eng.cancel()
    release.set()
    th.join(3)
    assert eng.context.status == WorkflowStatus.CANCELLED


def test_lint_warns_on_implicit_or_fan_in_and_missing_tool():
    nodes = {
        "s": WorkflowNode(node_id="s", name="s", node_type=NodeType.START),
        "a": WorkflowNode(node_id="a", name="a", node_type=NodeType.TASK),
        "b": WorkflowNode(node_id="b", name="b", node_type=NodeType.TASK, tool_name="x"),
        "e": WorkflowNode(node_id="e", name="e", node_type=NodeType.TASK, tool_name="y"),
        "end": WorkflowNode(node_id="end", name="end", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="1", from_node="s", to_node="a"), WorkflowEdge(edge_id="2", from_node="s", to_node="b"),
        WorkflowEdge(edge_id="3", from_node="a", to_node="e"), WorkflowEdge(edge_id="4", from_node="b", to_node="e"),
        WorkflowEdge(edge_id="5", from_node="e", to_node="end"),
    ]
    warnings = WorkflowDefinition(workflow_id="l", name="l", nodes=nodes, edges=edges).lint_graph()
    assert any("'a' no define 'tool_name'" in w for w in warnings)
    assert any("'e' tiene 2 aristas entrantes" in w for w in warnings)


def test_backtrack_resets_retries_and_prunes_checkpoints():
    nodes = {
        "s": WorkflowNode(node_id="s", name="s", node_type=NodeType.START),
        "t": WorkflowNode(node_id="t", name="t", node_type=NodeType.TASK, tool_name="ok"),
        "e": WorkflowNode(node_id="e", name="e", node_type=NodeType.END),
    }
    edges = [WorkflowEdge(edge_id="1", from_node="s", to_node="t"), WorkflowEdge(edge_id="2", from_node="t", to_node="e")]
    eng = WorkflowEngine(WorkflowDefinition(workflow_id="b", name="b", nodes=nodes, edges=edges),
                         task_handlers={"ok": lambda _i: {"ok": True}})
    eng.run_to_completion(concurrent=False)
    eng.context.node_retries["t"] = 3
    assert eng.backtrack_to_node("t") is True
    assert eng.context.node_retries["t"] == 0
    assert eng.context.finished_at is None
    assert eng.context.checkpoints[-1].target_node_id == "t"
