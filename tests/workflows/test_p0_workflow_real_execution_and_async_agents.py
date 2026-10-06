"""Pruebas de certificación formal P0: Semántica real de ejecución de TASK y sincronización asíncrona de AGENT.

Verifica:
1. Eliminación de simulación sintética: tareas sin handler o sin ejecutor registrado fallan formalmente (NodeStatus.FAILED)
   y nunca se dan por completadas artificialmente.
2. Nodos TASK con ejecutor registrado (handler o ToolRegistry) computan y almacenan outputs reales.
3. Nodos AGENT despachados a AgentMessageBus entran en estado WAITING_RESULT y suspenden el avance downstream.
4. Nodos descendientes permanecen bloqueados mientras el agente esté procesando.
5. Recepción de mensaje RESPONSE de agente resuelve el nodo a COMPLETED y permite la conclusión del workflow.
6. handle_agent_response permite resolución explícita por inyección de respuesta.
"""

import pytest
from praxeon.agents.bus import AgentMessageBus
from praxeon.agents.protocol import AgentMessage, MessagePriority, MessageType
from praxeon.policy.registry import ToolRegistry, ToolSpec
from praxeon.domain.assessment import RiskLevel
from praxeon.workflows.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from praxeon.workflows.engine import WorkflowEngine, WorkflowStatus


def test_p0_task_without_handler_fails_strictly_without_synthetic_completion():
    """P0-WORKFLOW: Tarea sin ejecutor registrado debe fallar deterministamente y no fingir éxito."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "unimplemented_task": WorkflowNode(
            node_id="unimplemented_task",
            name="Execute DB Migration",
            node_type=NodeType.TASK,
            tool_name="db_migrate",
        ),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="unimplemented_task"),
        WorkflowEdge(edge_id="e2", from_node="unimplemented_task", to_node="end"),
    ]
    wf = WorkflowDefinition(workflow_id="wf_strict_task", name="Strict Task Flow", nodes=nodes, edges=edges)

    # Por defecto, allow_synthetic_fallback es False
    engine = WorkflowEngine(workflow=wf)

    # Paso 1: start completa
    step1 = engine.step()
    assert step1 == "start"
    assert engine.context.node_states["start"] == NodeStatus.COMPLETED

    # Paso 2: unimplemented_task debe fallar porque carece de handler
    step2 = engine.step()
    assert step2 == "unimplemented_task"
    assert engine.context.node_states["unimplemented_task"] == NodeStatus.FAILED
    assert engine.context.status == WorkflowStatus.FAILED
    assert "No execution handler or ToolSpec registered" in engine.context.error_message


def test_p0_task_with_registered_handler_executes_real_computation():
    """P0-WORKFLOW: Tarea con handler registrado ejecuta y almacena su resultado real."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "compute_hash": WorkflowNode(
            node_id="compute_hash",
            name="Compute SHA256",
            node_type=NodeType.TASK,
            tool_name="hash_tool",
            inputs={"raw": "praxeon_runtime"},
        ),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="compute_hash"),
        WorkflowEdge(edge_id="e2", from_node="compute_hash", to_node="end"),
    ]
    wf = WorkflowDefinition(workflow_id="wf_real_task", name="Real Task Flow", nodes=nodes, edges=edges)
    engine = WorkflowEngine(workflow=wf)

    import hashlib
    engine.register_task_handler(
        "hash_tool",
        lambda inp: {"digest": hashlib.sha256(inp["raw"].encode()).hexdigest()},
    )

    ctx = engine.run_to_completion()
    assert ctx.status == WorkflowStatus.COMPLETED
    assert ctx.node_states["compute_hash"] == NodeStatus.COMPLETED
    assert "digest" in ctx.node_outputs["compute_hash"]
    expected = hashlib.sha256(b"praxeon_runtime").hexdigest()
    assert ctx.node_outputs["compute_hash"]["digest"] == expected


def test_p0_agent_node_enters_waiting_result_and_blocks_downstream():
    """P0-WORKFLOW: Nodo AGENT debe transicionar a WAITING_RESULT y bloquear pasos dependientes."""
    bus = AgentMessageBus()
    bus.register_agent("ag_worker")
    bus.register_agent("praxeon_supervisor")

    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "agent_step": WorkflowNode(
            node_id="agent_step",
            name="Autonomous Agent Step",
            node_type=NodeType.AGENT,
            agent_id="ag_worker",
            inputs={"objective": "Generate unit test"},
        ),
        "post_task": WorkflowNode(
            node_id="post_task",
            name="Verify Test",
            node_type=NodeType.TASK,
            tool_name="verify_tool",
        ),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="agent_step"),
        WorkflowEdge(edge_id="e2", from_node="agent_step", to_node="post_task"),
        WorkflowEdge(edge_id="e3", from_node="post_task", to_node="end"),
    ]
    wf = WorkflowDefinition(workflow_id="wf_async_agent", name="Async Agent Flow", nodes=nodes, edges=edges)
    engine = WorkflowEngine(workflow=wf, agent_bus=bus)
    engine.register_task_handler("verify_tool", lambda inp: {"verified": True})

    # Paso 1: start
    engine.step()
    assert engine.context.node_states["start"] == NodeStatus.COMPLETED

    # Paso 2: dispatch a agent_step
    engine.step()
    assert engine.context.node_states["agent_step"] == NodeStatus.WAITING_RESULT
    assert engine.context.status == WorkflowStatus.RUNNING

    # Comprobar que post_task y end siguen bloqueados en PENDING
    assert engine.context.node_states["post_task"] == NodeStatus.PENDING
    assert engine.context.node_states["end"] == NodeStatus.PENDING

    # Avanzar el engine sin que el agente haya respondido debe retornar None (workflow suspendido esperando)
    advance = engine.step()
    assert advance is None
    assert engine.context.node_states["agent_step"] == NodeStatus.WAITING_RESULT

    # Ahora el agente lee de su buzón
    msg_in = bus.receive("ag_worker")
    assert msg_in is not None
    assert msg_in.message_type == MessageType.DELEGATE
    assert msg_in.payload["objective"] == "Generate unit test"

    # El agente responde a praxeon_supervisor
    resp = AgentMessage(
        message_id="resp_agent_123",
        sender_id="ag_worker",
        receiver_id="praxeon_supervisor",
        session_id=engine.context.execution_id,
        task_id="agent_step",
        message_type=MessageType.RESPONSE,
        payload={"generated_code": "def test_it(): assert True"},
    )
    bus.send(resp)

    # Ahora el engine detecta la respuesta y avanza secuencialmente
    # Step 1 tras respuesta: consume la respuesta del agente y completa agent_step
    res_agent = engine.step()
    assert res_agent == "agent_step"
    assert engine.context.node_states["agent_step"] == NodeStatus.COMPLETED
    assert engine.context.node_outputs["agent_step"]["generated_code"] == "def test_it(): assert True"

    # Step 2: ahora post_task puede ejecutarse
    res_post = engine.step()
    assert res_post == "post_task"
    assert engine.context.node_states["post_task"] == NodeStatus.COMPLETED

    # Step 3: end concluye el flujo
    res_end = engine.step()
    assert res_end == "end"
    assert engine.context.node_states["end"] == NodeStatus.COMPLETED
    assert engine.context.status == WorkflowStatus.COMPLETED


def test_p0_handle_agent_response_direct_injection():
    """P0-WORKFLOW: handle_agent_response procesa e inyecta respuestas asíncronas directamente."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "agent_node": WorkflowNode(
            node_id="agent_node",
            name="Analysis Agent",
            node_type=NodeType.AGENT,
            agent_id="ag_analyst",
        ),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="agent_node"),
        WorkflowEdge(edge_id="e2", from_node="agent_node", to_node="end"),
    ]
    wf = WorkflowDefinition(workflow_id="wf_direct_resp", name="Direct Response Flow", nodes=nodes, edges=edges)

    bus = AgentMessageBus()
    bus.register_agent("ag_analyst")
    bus.register_agent("praxeon_supervisor")

    engine = WorkflowEngine(workflow=wf, agent_bus=bus)

    engine.step()  # start
    engine.step()  # agent_node -> WAITING_RESULT
    assert engine.context.node_states["agent_node"] == NodeStatus.WAITING_RESULT

    # Inyección directa mediante método handle_agent_response
    resp = AgentMessage(
        message_id="resp_direct_456",
        sender_id="ag_analyst",
        receiver_id="praxeon_supervisor",
        session_id=engine.context.execution_id,
        task_id="agent_node",
        message_type=MessageType.RESPONSE,
        payload={"analysis": "zero_vulnerabilities_detected"},
    )
    handled = engine.handle_agent_response(resp)
    assert handled is True
    assert engine.context.node_states["agent_node"] == NodeStatus.COMPLETED
    assert engine.context.node_outputs["agent_node"]["analysis"] == "zero_vulnerabilities_detected"

    # El siguiente step ejecuta el END
    step_end = engine.step()
    assert step_end == "end"
    assert engine.context.status == WorkflowStatus.COMPLETED
