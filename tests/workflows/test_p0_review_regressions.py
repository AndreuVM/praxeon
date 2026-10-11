"""Regresiones P0 del módulo de workflows: ejecución sintética, límite WHILE y cancelación."""

from praxeon.workflows.editor_service import WorkflowEditorService
from praxeon.workflows.engine import WorkflowEngine
from praxeon.workflows.models import (
    NodeType, WorkflowDefinition, WorkflowEdge, WorkflowNode, WorkflowStatus,
)


def _task_workflow(svc):
    wf = svc.create_workflow("t")
    node = svc.add_node(wf.workflow_id, "borrar", NodeType.TASK, tool_name="rm_rf_prod")
    svc.disconnect_nodes(wf.workflow_id, "e_start_end")
    svc.connect_nodes(wf.workflow_id, "start", node.node_id)
    svc.connect_nodes(wf.workflow_id, node.node_id, "end")
    return wf.workflow_id, node.node_id


def test_editor_does_not_simulate_tasks_by_default(monkeypatch):
    monkeypatch.delenv("PRAXEON_WORKFLOW_SIMULATION", raising=False)
    svc = WorkflowEditorService()
    wid, nid = _task_workflow(svc)
    res = svc.execute_workflow(wid)
    # Herramienta no catalogada: el puente exige aprobación humana y nada se ejecuta
    assert res["status"] == WorkflowStatus.PAUSED.value
    assert res["node_states"][nid] == "WAITING_APPROVAL"
    assert nid not in res["node_outputs"]


def test_simulation_is_explicit_and_flagged():
    svc = WorkflowEditorService(allow_simulation=True)
    wid, nid = _task_workflow(svc)
    svc.update_node(wid, nid, tool_name="echo")
    res = svc.execute_workflow(wid)
    assert res["status"] == WorkflowStatus.COMPLETED.value
    assert res["node_outputs"][nid]["simulated"] is True


def _while_workflow(max_iter, cond):
    nodes = {
        "s": WorkflowNode(node_id="s", name="s", node_type=NodeType.START),
        "w": WorkflowNode(node_id="w", name="w", node_type=NodeType.WHILE,
                          control_config={"max_iterations": max_iter, "body_entry": "b",
                                          "exit_target": "e", "condition": cond}),
        "b": WorkflowNode(node_id="b", name="b", node_type=NodeType.TASK, tool_name="inc"),
        "e": WorkflowNode(node_id="e", name="e", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="1", from_node="s", to_node="w"),
        WorkflowEdge(edge_id="2", from_node="w", to_node="b", label="body"),
        WorkflowEdge(edge_id="3", from_node="b", to_node="w"),
        WorkflowEdge(edge_id="4", from_node="w", to_node="e", label="exit"),
    ]
    return WorkflowDefinition(workflow_id="x", name="x", nodes=nodes, edges=edges, variables={"n": 0})


def _run_while(max_iter, cond):
    holder = {}

    def inc(_inputs):
        holder["e"].context.variables["n"] += 1
        return {"ok": True}

    eng = WorkflowEngine(_while_workflow(max_iter, cond), task_handlers={"inc": inc})
    holder["e"] = eng
    return eng.run_to_completion(max_steps=200, concurrent=False)


def test_while_exits_naturally_when_condition_false_at_limit():
    ctx = _run_while(3, {"field": "variables.n", "operator": "<", "expected_value": 3})
    assert ctx.status == WorkflowStatus.COMPLETED
    assert ctx.variables["n"] == 3


def test_while_still_aborts_when_condition_never_ends():
    ctx = _run_while(3, {"field": "variables.n", "operator": ">=", "expected_value": 0})
    assert ctx.status == WorkflowStatus.FAILED


def test_cancel_does_not_override_completed_workflow():
    svc = WorkflowEditorService(allow_simulation=True)
    wf = svc.create_workflow("c")
    eng = WorkflowEngine(svc.get_workflow(wf.workflow_id), allow_synthetic_fallback=True)
    eng.run_to_completion()
    assert eng.context.status == WorkflowStatus.COMPLETED
    eng.cancel()
    assert eng.context.status == WorkflowStatus.COMPLETED


def test_editor_preserves_timeout_and_metadata_on_edit():
    svc = WorkflowEditorService()
    wf = svc.create_workflow("m")
    svc.register_workflow(WorkflowDefinition(**{**wf.model_dump(), "timeout_seconds": 30.0, "metadata": {"owner": "x"}}))
    svc.update_node_position(wf.workflow_id, "start", 5, 5)
    svc.add_node(wf.workflow_id, "t", NodeType.TASK, tool_name="x")
    edited = svc.get_workflow(wf.workflow_id)
    assert edited.timeout_seconds == 30.0
    assert edited.metadata == {"owner": "x"}


def test_engine_is_refreshed_after_edit():
    svc = WorkflowEditorService()
    wf = svc.create_workflow("s")
    assert len(svc.get_or_create_engine(wf.workflow_id).workflow.nodes) == 2
    node = svc.add_node(wf.workflow_id, "t", NodeType.TASK, tool_name="x")
    svc.connect_nodes(wf.workflow_id, "start", node.node_id)
    assert len(svc.get_or_create_engine(wf.workflow_id).workflow.nodes) == 3


def test_incompatible_edge_condition_types_do_not_crash_engine():
    from praxeon.workflows.models import EdgeCondition
    nodes = {
        "s": WorkflowNode(node_id="s", name="s", node_type=NodeType.START),
        "e": WorkflowNode(node_id="e", name="e", node_type=NodeType.END),
    }
    edge = WorkflowEdge(edge_id="1", from_node="s", to_node="e",
                        condition=EdgeCondition(field="v", operator=">", expected_value=5))
    wf = WorkflowDefinition(workflow_id="y", name="y", nodes=nodes, edges=[edge], variables={"v": "abc"})
    WorkflowEngine(wf).run_to_completion(concurrent=False)  # no debe lanzar TypeError


def test_engine_without_handlers_fails_closed_without_simulation():
    svc = WorkflowEditorService()
    wid, nid = _task_workflow(svc)
    svc.update_node(wid, nid, tool_name="echo")
    res = svc.execute_workflow(wid)
    assert res["status"] == WorkflowStatus.FAILED.value


def _agent_engine(bus, exec_id):
    nodes = {
        "s": WorkflowNode(node_id="s", name="s", node_type=NodeType.START),
        "a": WorkflowNode(node_id="a", name="a", node_type=NodeType.AGENT, agent_id="ag_worker"),
        "e": WorkflowNode(node_id="e", name="e", node_type=NodeType.END),
    }
    edges = [WorkflowEdge(edge_id="1", from_node="s", to_node="a"), WorkflowEdge(edge_id="2", from_node="a", to_node="e")]
    wf = WorkflowDefinition(workflow_id="wf_ag", name="ag", nodes=nodes, edges=edges)
    eng = WorkflowEngine(wf, agent_bus=bus, execution_id=exec_id)
    eng.step()
    eng.step()
    return eng


def _reply(exec_id, sender="ag_worker", payload=None, mtype=None):
    from praxeon.agents.protocol import AgentMessage, MessageType
    return AgentMessage(message_id=f"r_{exec_id}_{sender}", sender_id=sender, receiver_id="praxeon_supervisor",
                        session_id=exec_id, task_id="a", message_type=mtype or MessageType.RESPONSE,
                        payload=payload or {"ok": True})


def test_agent_responses_are_isolated_per_execution_and_sender():
    from praxeon.agents.bus import AgentMessageBus
    from praxeon.workflows.models import NodeStatus
    bus = AgentMessageBus()
    for a in ("praxeon_supervisor", "ag_worker", "intruder"):
        bus.register_agent(a)
    e1, e2 = _agent_engine(bus, "exec_1"), _agent_engine(bus, "exec_2")
    # Respuesta de otro agente: no puede resolver el nodo
    bus.send(_reply("exec_1", sender="intruder"))
    assert e1.step() is None
    assert e1.context.node_states["a"] == NodeStatus.WAITING_RESULT
    # Respuesta legítima de exec_2: exec_1 no la consume ni la aplica
    bus.send(_reply("exec_2"))
    e1.step()
    assert e1.context.node_states["a"] == NodeStatus.WAITING_RESULT
    e2.step()
    assert e2.context.node_states["a"] == NodeStatus.COMPLETED


def test_agent_error_reply_fails_node():
    from praxeon.agents.bus import AgentMessageBus
    from praxeon.agents.protocol import MessageType
    bus = AgentMessageBus()
    for a in ("praxeon_supervisor", "ag_worker"):
        bus.register_agent(a)
    eng = _agent_engine(bus, "exec_x")
    bus.send(_reply("exec_x", payload={"error": "boom"}, mtype=MessageType.AGENT_NACK))
    eng.step()
    assert eng.context.status == WorkflowStatus.FAILED
