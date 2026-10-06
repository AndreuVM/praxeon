"""Pruebas unitarias de concurrencia física en grafos de workflows (DEUDA-WF-01).

Valida:
- find_all_ready_nodes() retornando múltiples nodos elegibles tras un PARALLEL_FORK.
- step_concurrent() ejecutando físicamente en paralelo tareas concurrentes en un ThreadPoolExecutor.
- Verificación de tiempo transcurrido (3 tareas con latencia de 100ms completan en ~100ms y no 300ms).
- Despacho concurrente de agentes en AgentMessageBus pasando atómicamente a WAITING_RESULT.
- Convergencia y sincronización correcta en PARALLEL_JOIN.
"""

import time
import pytest
from datetime import datetime, timezone

from praxeon.agents import (
    AgentMessage,
    AgentMessageBus,
    MessageType,
    TopologyType,
)
from praxeon.workflows import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowEngine,
    WorkflowNode,
    WorkflowStatus,
)


def build_parallel_fork_workflow() -> WorkflowDefinition:
    """Construye un flujo con bifurcación paralela:
    START -> FORK -> [TASK_A, TASK_B, TASK_C] -> JOIN -> END
    """
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "fork": WorkflowNode(node_id="fork", name="Parallel Fork", node_type=NodeType.PARALLEL_FORK),
        "task_a": WorkflowNode(
            node_id="task_a",
            name="Task A",
            node_type=NodeType.TASK,
            tool_name="slow_task_a",
        ),
        "task_b": WorkflowNode(
            node_id="task_b",
            name="Task B",
            node_type=NodeType.TASK,
            tool_name="slow_task_b",
        ),
        "task_c": WorkflowNode(
            node_id="task_c",
            name="Task C",
            node_type=NodeType.TASK,
            tool_name="slow_task_c",
        ),
        "join": WorkflowNode(node_id="join", name="Parallel Join", node_type=NodeType.PARALLEL_JOIN),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }

    edges = [
        WorkflowEdge(edge_id="e_s_f", from_node="start", to_node="fork"),
        WorkflowEdge(edge_id="e_f_a", from_node="fork", to_node="task_a"),
        WorkflowEdge(edge_id="e_f_b", from_node="fork", to_node="task_b"),
        WorkflowEdge(edge_id="e_f_c", from_node="fork", to_node="task_c"),
        WorkflowEdge(edge_id="e_a_j", from_node="task_a", to_node="join"),
        WorkflowEdge(edge_id="e_b_j", from_node="task_b", to_node="join"),
        WorkflowEdge(edge_id="e_c_j", from_node="task_c", to_node="join"),
        WorkflowEdge(edge_id="e_j_e", from_node="join", to_node="end"),
    ]

    return WorkflowDefinition(
        workflow_id="wf_parallel_benchmark",
        name="Parallel Benchmark Workflow",
        nodes=nodes,
        edges=edges,
    )


def test_find_all_ready_nodes_returns_all_fork_branches():
    """Valida que tras ejecutar START y FORK, find_all_ready_nodes retorne simultáneamente las 3 ramas."""
    wf = build_parallel_fork_workflow()
    engine = WorkflowEngine(workflow=wf)

    # 1. Ejecutar START
    engine.step()
    assert engine.context.node_states["start"] == NodeStatus.COMPLETED

    # 2. Ejecutar FORK
    ready_after_start = engine.find_all_ready_nodes()
    assert ready_after_start == ["fork"]
    engine.step()
    assert engine.context.node_states["fork"] == NodeStatus.COMPLETED

    # 3. Tras el FORK, las tres tareas deben estar listas simultáneamente
    ready_nodes = engine.find_all_ready_nodes()
    assert set(ready_nodes) == {"task_a", "task_b", "task_c"}


def test_step_concurrent_executes_parallel_tasks_simultaneously():
    """Demuestra que 3 tareas con latencia de 100ms ejecutan en ~100-160ms en lugar de 300ms seriales."""
    wf = build_parallel_fork_workflow()
    engine = WorkflowEngine(workflow=wf)

    def slow_worker(node_name: str, delay_s: float):
        def _handler(inputs):
            t_start = time.perf_counter()
            time.sleep(delay_s)
            t_end = time.perf_counter()
            return {"node": node_name, "t_start": t_start, "t_end": t_end}
        return _handler

    engine.register_task_handler("slow_task_a", slow_worker("task_a", 0.10))
    engine.register_task_handler("slow_task_b", slow_worker("task_b", 0.10))
    engine.register_task_handler("slow_task_c", slow_worker("task_c", 0.10))

    # Avanzar START y FORK
    engine.step()
    engine.step()
    assert set(engine.find_all_ready_nodes()) == {"task_a", "task_b", "task_c"}

    # Medir ejecución concurrente de las 3 ramas
    wall_start = time.perf_counter()
    executed = engine.step_concurrent(max_workers=4)
    wall_duration = time.perf_counter() - wall_start

    assert set(executed) == {"task_a", "task_b", "task_c"}
    assert engine.context.node_states["task_a"] == NodeStatus.COMPLETED
    assert engine.context.node_states["task_b"] == NodeStatus.COMPLETED
    assert engine.context.node_states["task_c"] == NodeStatus.COMPLETED

    # Si se hubiesen ejecutado en serie, habría tardado >= 0.30s.
    # En paralelo, toma ~0.10s (+ sobrecarga mínima de hilos < 0.25s).
    assert wall_duration < 0.25, f"La ejecución paralela tardó {wall_duration:.3f}s (esperado < 0.25s)"


def test_run_to_completion_with_concurrency_resolves_join_and_end():
    """Valida el ciclo de vida completo de un workflow bifurcado ejecutado concurrentemente hasta END."""
    wf = build_parallel_fork_workflow()
    engine = WorkflowEngine(workflow=wf)

    engine.register_task_handler("slow_task_a", lambda _: {"a": 1})
    engine.register_task_handler("slow_task_b", lambda _: {"b": 2})
    engine.register_task_handler("slow_task_c", lambda _: {"c": 3})

    ctx = engine.run_to_completion(concurrent=True, max_workers=4)

    assert ctx.status == WorkflowStatus.COMPLETED
    assert ctx.node_states["start"] == NodeStatus.COMPLETED
    assert ctx.node_states["fork"] == NodeStatus.COMPLETED
    assert ctx.node_states["task_a"] == NodeStatus.COMPLETED
    assert ctx.node_states["task_b"] == NodeStatus.COMPLETED
    assert ctx.node_states["task_c"] == NodeStatus.COMPLETED
    assert ctx.node_states["join"] == NodeStatus.COMPLETED
    assert ctx.node_states["end"] == NodeStatus.COMPLETED


def test_concurrent_agent_dispatch_puts_all_branches_in_waiting_result():
    """Valida que ramas paralelas de tipo AGENT se despachan concurrentemente a AgentMessageBus."""
    bus = AgentMessageBus(default_topology=TopologyType.MESH)
    bus.register_agent("agent_coder_01")
    bus.register_agent("agent_tester_01")
    bus.register_agent("praxeon_supervisor")

    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "fork": WorkflowNode(node_id="fork", name="Fork", node_type=NodeType.PARALLEL_FORK),
        "agent_coder": WorkflowNode(
            node_id="agent_coder",
            name="Coder Agent",
            node_type=NodeType.AGENT,
            agent_id="agent_coder_01",
        ),
        "agent_tester": WorkflowNode(
            node_id="agent_tester",
            name="Tester Agent",
            node_type=NodeType.AGENT,
            agent_id="agent_tester_01",
        ),
        "join": WorkflowNode(node_id="join", name="Join", node_type=NodeType.PARALLEL_JOIN),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }

    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="fork"),
        WorkflowEdge(edge_id="e2", from_node="fork", to_node="agent_coder"),
        WorkflowEdge(edge_id="e3", from_node="fork", to_node="agent_tester"),
        WorkflowEdge(edge_id="e4", from_node="agent_coder", to_node="join"),
        WorkflowEdge(edge_id="e5", from_node="agent_tester", to_node="join"),
        WorkflowEdge(edge_id="e6", from_node="join", to_node="end"),
    ]

    wf = WorkflowDefinition(workflow_id="wf_multi_agent", name="Multi-Agent Flow", nodes=nodes, edges=edges)
    engine = WorkflowEngine(workflow=wf, agent_bus=bus)

    # Iniciar y avanzar a través de START y FORK
    engine.step()
    engine.step()

    # Ambas tareas de agente están listas
    ready = engine.find_all_ready_nodes()
    assert set(ready) == {"agent_coder", "agent_tester"}

    # Despachar concurrentemente
    dispatched = engine.step_concurrent(max_workers=2)
    assert set(dispatched) == {"agent_coder", "agent_tester"}

    # Ambos nodos deben estar en WAITING_RESULT
    assert engine.context.node_states["agent_coder"] == NodeStatus.WAITING_RESULT
    assert engine.context.node_states["agent_tester"] == NodeStatus.WAITING_RESULT

    # Los buzones de ambos agentes deben haber recibido su mensaje
    msg_coder = bus.receive("agent_coder_01")
    msg_tester = bus.receive("agent_tester_01")
    assert msg_coder is not None
    assert msg_coder.task_id == "agent_coder"
    assert msg_tester is not None
    assert msg_tester.task_id == "agent_tester"
