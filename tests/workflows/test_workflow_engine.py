"""Pruebas unitarias para el Motor y Máquina de Estados de Workflows (Fase 6 - F6-02).

Valida:
- Ejecución completa paso a paso y run_to_completion de flujos.
- Máquina de estados: start, pause, resume y cancel.
- Política de reintentos automáticos y reactivación manual (retry_node).
- Retroceso determinista (backtrack_to_node) con invalidación limpia de dependencias downstream.
- Bifurcaciones condicionales y marcado automático de ramas no tomadas como SKIPPED.
- Integración y despacho coordinado a buzones de agentes en AgentMessageBus.
"""

import pytest

from praxeon.agents import (
    AgentMessage,
    AgentMessageBus,
    MessageType,
    TopologyType,
)
from praxeon.workflows import (
    EdgeCondition,
    NodeStatus,
    NodeType,
    RetryPolicy,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowEngine,
    WorkflowNode,
    WorkflowStatus,
)


def build_sample_workflow() -> WorkflowDefinition:
    """Construye un flujo canónico de 4 etapas: START -> TASK_BUILD -> TASK_TEST -> END."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "build": WorkflowNode(
            node_id="build",
            name="Build Code",
            node_type=NodeType.TASK,
            tool_name="build_tool",
            inputs={"target": "release"},
        ),
        "test": WorkflowNode(
            node_id="test",
            name="Run Tests",
            node_type=NodeType.TASK,
            tool_name="test_tool",
        ),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }

    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="build"),
        WorkflowEdge(edge_id="e2", from_node="build", to_node="test"),
        WorkflowEdge(edge_id="e3", from_node="test", to_node="end"),
    ]

    return WorkflowDefinition(
        workflow_id="wf_ci_pipeline",
        name="CI Pipeline",
        nodes=nodes,
        edges=edges,
    )


def test_engine_run_to_completion():
    """Valida la ejecución secuencial completa hasta estado COMPLETED con handlers personalizados."""
    wf = build_sample_workflow()
    engine = WorkflowEngine(workflow=wf)

    # Registrar handlers
    engine.register_task_handler("build_tool", lambda inputs: {"binary": "/bin/app", "size_kb": 1024})
    engine.register_task_handler("test_tool", lambda inputs: {"tests_passed": True, "coverage": 95.0})

    ctx = engine.run_to_completion()

    assert ctx.status == WorkflowStatus.COMPLETED
    assert ctx.node_states["start"] == NodeStatus.COMPLETED
    assert ctx.node_states["build"] == NodeStatus.COMPLETED
    assert ctx.node_states["test"] == NodeStatus.COMPLETED
    assert ctx.node_states["end"] == NodeStatus.COMPLETED
    assert ctx.node_outputs["build"]["binary"] == "/bin/app"
    assert ctx.node_outputs["test"]["tests_passed"] is True
    assert ctx.execution_history == ["start", "build", "test", "end"]


def test_engine_pause_and_resume():
    """Valida la detención pausada y la posterior reanudación fluida."""
    wf = build_sample_workflow()
    engine = WorkflowEngine(workflow=wf)
    engine.register_task_handler("build_tool", lambda inputs: {"binary": "/bin/app"})
    engine.register_task_handler("test_tool", lambda inputs: {"status": "ok"})

    # Ejecutar primer paso (start)
    step1 = engine.step()
    assert step1 == "start"
    assert engine.context.status == WorkflowStatus.RUNNING

    # Pausar
    engine.pause()
    assert engine.context.status == WorkflowStatus.PAUSED

    # Intentar avanzar en pausa debe ser nulo
    assert engine.step() is None

    # Reanudar
    engine.resume()
    assert engine.context.status == WorkflowStatus.RUNNING

    # Completar el resto
    ctx = engine.run_to_completion()
    assert ctx.status == WorkflowStatus.COMPLETED
    assert ctx.node_states["end"] == NodeStatus.COMPLETED


def test_engine_cancellation():
    """Valida la cancelación explícita del workflow y el marcado de nodos pendientes."""
    wf = build_sample_workflow()
    engine = WorkflowEngine(workflow=wf)

    engine.step()  # start completado
    assert engine.context.node_states["start"] == NodeStatus.COMPLETED

    engine.cancel(reason="Security anomaly detected")
    assert engine.context.status == WorkflowStatus.CANCELLED
    assert engine.context.error_message == "Security anomaly detected"
    assert engine.context.node_states["build"] == NodeStatus.CANCELLED
    assert engine.context.node_states["test"] == NodeStatus.CANCELLED
    assert engine.context.node_states["end"] == NodeStatus.CANCELLED


def test_engine_automatic_retry_policy():
    """Valida la política de reintentos automáticos tras fallo transitorio."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "flakey": WorkflowNode(
            node_id="flakey",
            name="Flakey Network Call",
            node_type=NodeType.TASK,
            tool_name="network_op",
            retry_policy=RetryPolicy(max_retries=1, delay_seconds=0.0),
        ),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="flakey"),
        WorkflowEdge(edge_id="e2", from_node="flakey", to_node="end"),
    ]
    wf = WorkflowDefinition(workflow_id="wf_retry", name="Retry Test", nodes=nodes, edges=edges)

    attempts = 0

    def flakey_handler(inputs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("Temporary timeout")
        return {"status": "recovered"}

    engine = WorkflowEngine(workflow=wf)
    engine.register_task_handler("network_op", flakey_handler)

    ctx = engine.run_to_completion()
    assert ctx.status == WorkflowStatus.COMPLETED
    assert attempts == 2  # Primer fallo + 1 reintento exitoso
    assert ctx.node_states["flakey"] == NodeStatus.COMPLETED


def test_engine_deterministic_backtrack():
    """Valida el retroceso determinista a un nodo previo y la invalidación limpia de su descendencia."""
    wf = build_sample_workflow()
    engine = WorkflowEngine(workflow=wf)

    engine.register_task_handler("build_tool", lambda inputs: {"version": "v1.0"})
    engine.register_task_handler("test_tool", lambda inputs: {"result": "ok"})

    engine.run_to_completion()
    assert engine.context.status == WorkflowStatus.COMPLETED
    assert "test" in engine.context.node_outputs

    # Ejecutar backtrack hacia el nodo "build"
    ok = engine.backtrack_to_node("build")
    assert ok is True
    assert engine.context.status == WorkflowStatus.RUNNING

    # El nodo build debe estar READY
    assert engine.context.node_states["build"] == NodeStatus.READY
    # Sus descendientes deben haber vuelto a PENDING y sus salidas limpiadas
    assert engine.context.node_states["test"] == NodeStatus.PENDING
    assert engine.context.node_states["end"] == NodeStatus.PENDING
    assert "test" not in engine.context.node_outputs

    # Modificar variable de contexto simulando corrección
    engine.context.variables["hotfix"] = True
    engine.register_task_handler("build_tool", lambda inputs: {"version": "v1.1-patched"})

    # Volver a completar tras el backtrack
    ctx2 = engine.run_to_completion()
    assert ctx2.status == WorkflowStatus.COMPLETED
    assert ctx2.node_outputs["build"]["version"] == "v1.1-patched"
    assert ctx2.node_states["end"] == NodeStatus.COMPLETED


def test_engine_conditional_branching_and_skips():
    """Valida la bifurcación condicional basada en el resultado de una decisión y el marcado de omitidos."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "decision": WorkflowNode(node_id="decision", name="Analyze Risk", node_type=NodeType.DECISION),
        "high_risk_branch": WorkflowNode(node_id="high_risk_branch", name="Escalate SecOps", node_type=NodeType.TASK),
        "low_risk_branch": WorkflowNode(node_id="low_risk_branch", name="Auto Deploy", node_type=NodeType.TASK),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }

    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="decision"),
        WorkflowEdge(
            edge_id="e_high",
            from_node="decision",
            to_node="high_risk_branch",
            condition=EdgeCondition(field="risk_level", operator="==", expected_value="HIGH"),
        ),
        WorkflowEdge(
            edge_id="e_low",
            from_node="decision",
            to_node="low_risk_branch",
            condition=EdgeCondition(field="risk_level", operator="==", expected_value="LOW"),
        ),
        WorkflowEdge(edge_id="e_end1", from_node="high_risk_branch", to_node="end"),
        WorkflowEdge(edge_id="e_end2", from_node="low_risk_branch", to_node="end"),
    ]

    wf = WorkflowDefinition(workflow_id="wf_branching", name="Branching Flow", nodes=nodes, edges=edges)
    engine = WorkflowEngine(workflow=wf)
    engine.register_task_handler("Auto Deploy", lambda inputs: {"deployed": True})
    engine.register_task_handler("Escalate SecOps", lambda inputs: {"escalated": True})

    # Iniciar con variable de riesgo bajo
    ctx = engine.start(initial_variables={"risk_level": "LOW"})
    engine.run_to_completion()

    assert ctx.status == WorkflowStatus.COMPLETED
    # La rama de bajo riesgo debió completarse
    assert ctx.node_states["low_risk_branch"] == NodeStatus.COMPLETED
    # La rama de alto riesgo debió omitirse (SKIPPED)
    assert ctx.node_states["high_risk_branch"] == NodeStatus.SKIPPED
    assert ctx.node_states["end"] == NodeStatus.COMPLETED


def test_engine_agent_bus_integration():
    """Valida el despacho directo de tareas desde nodos AGENT al AgentMessageBus y su resolución asíncrona."""
    bus = AgentMessageBus(default_topology=TopologyType.MESH)
    bus.register_agent("ag_reviewer")
    bus.register_agent("praxeon_supervisor")

    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "agent_review": WorkflowNode(
            node_id="agent_review",
            name="Security Review Agent",
            node_type=NodeType.AGENT,
            agent_id="ag_reviewer",
            inputs={"pull_request_id": 42},
        ),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }

    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="agent_review"),
        WorkflowEdge(edge_id="e2", from_node="agent_review", to_node="end"),
    ]

    wf = WorkflowDefinition(workflow_id="wf_agent_dispatch", name="Agent Dispatch", nodes=nodes, edges=edges)
    engine = WorkflowEngine(workflow=wf, agent_bus=bus)

    # Paso 1: start
    engine.step()
    assert engine.context.node_states["start"] == NodeStatus.COMPLETED

    # Paso 2: dispatch a agente -> el nodo pasa a WAITING_RESULT (no completado falsamente)
    engine.step()
    assert engine.context.node_states["agent_review"] == NodeStatus.WAITING_RESULT
    assert engine.context.status == WorkflowStatus.RUNNING

    # Comprobar que en el buzón de ag_reviewer se depositó el mensaje de delegación
    received_msg = bus.receive("ag_reviewer")
    assert received_msg is not None
    assert received_msg.task_id == "agent_review"
    assert received_msg.payload["pull_request_id"] == 42

    # Agente procesa y deposita la respuesta dirigida a praxeon_supervisor
    reply = AgentMessage(
        message_id="wf_resp_001",
        sender_id="ag_reviewer",
        receiver_id="praxeon_supervisor",
        session_id=engine.context.execution_id,
        task_id="agent_review",
        message_type=MessageType.RESPONSE,
        payload={"review_verdict": "APPROVED", "comments": "Code approved by agent."},
    )
    bus.send(reply)

    # Paso 3: el engine procesa la respuesta en el step y concluye el workflow
    ctx = engine.run_to_completion()

    assert ctx.status == WorkflowStatus.COMPLETED
    assert ctx.node_states["agent_review"] == NodeStatus.COMPLETED
    assert ctx.node_outputs["agent_review"]["review_verdict"] == "APPROVED"
    assert ctx.node_states["end"] == NodeStatus.COMPLETED
