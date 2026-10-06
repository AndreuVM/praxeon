"""Test suite para [P1-WORKFLOW] Scheduler real para Timeouts y Políticas de Reintento.

Verifica:
1. Transición a RETRYING con cálculo de backoff exponencial en fallos de nodo.
2. Despertar de nodos RETRYING a READY mediante check_scheduled_retries_and_timeouts.
3. Timeout en nodos activos (p. ej. AGENT en WAITING_RESULT) con reintentos programados y eventual TIMEOUT.
4. Soporte de retry_node para estados TIMEOUT y RETRYING.
5. Persistencia y serialización de node_started_at y node_retry_after en WorkflowExecution y WorkflowExecutionContext.
"""

from datetime import datetime, timezone, timedelta
import pytest

from praxeon.agents.bus import AgentMessageBus
from praxeon.workflows.models import (
    NodeType,
    NodeStatus,
    WorkflowStatus,
    WorkflowNode,
    WorkflowEdge,
    WorkflowDefinition,
    WorkflowExecution,
    RetryPolicy,
)
from praxeon.workflows.engine import WorkflowEngine, WorkflowExecutionContext


def create_retry_workflow() -> WorkflowDefinition:
    """Crea un workflow de prueba con políticas de reintento configuradas."""
    start_node = WorkflowNode(node_id="start", name="Start", node_type=NodeType.START)
    flaky_task = WorkflowNode(
        node_id="flaky_task",
        name="Flaky Task",
        node_type=NodeType.TASK,
        tool_name="test_tool",
        retry_policy=RetryPolicy(max_retries=2, delay_seconds=5.0, backoff_multiplier=2.0),
        timeout_seconds=10,
    )
    end_node = WorkflowNode(node_id="end", name="End", node_type=NodeType.END)

    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="flaky_task"),
        WorkflowEdge(edge_id="e2", from_node="flaky_task", to_node="end"),
    ]

    return WorkflowDefinition(
        workflow_id="wf_retry_test",
        name="Workflow Retry & Timeout Test",
        nodes={"start": start_node, "flaky_task": flaky_task, "end": end_node},
        edges=edges,
    )


def test_retry_policy_exponential_backoff_and_scheduler_wakeup():
    """Valida el cálculo de backoff exponencial y la reactivación programada de nodos RETRYING."""
    wf = create_retry_workflow()
    attempts = 0

    def flaky_handler(inputs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError(f"Fallo simulado intento #{attempts}")
        return {"result": "success"}

    engine = WorkflowEngine(wf, task_handlers={"test_tool": flaky_handler})
    engine.start()

    # Paso 1: Ejecuta Start
    n1 = engine.step()
    assert n1 == "start"
    assert engine.context.node_states["start"] == NodeStatus.COMPLETED
    assert engine.is_node_ready("flaky_task") is True

    t0 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)

    # Paso 2: Ejecuta flaky_task (intento 1 -> falla)
    engine.step(current_time=t0)
    assert attempts == 1
    assert engine.context.node_retries["flaky_task"] == 1
    # Con delay_seconds=5.0 y retries=0 inicial: delay = 5.0 * (2.0 ** 0) = 5.0s
    assert engine.context.node_states["flaky_task"] == NodeStatus.RETRYING
    retry_deadline_1 = engine.context.node_retry_after["flaky_task"]
    assert retry_deadline_1 == t0 + timedelta(seconds=5)

    # Paso 3: Avance antes de que expire el delay (t0 + 2s) -> permanece en RETRYING
    t_early = t0 + timedelta(seconds=2)
    next_step = engine.step(current_time=t_early)
    assert next_step is None
    assert engine.context.node_states["flaky_task"] == NodeStatus.RETRYING

    # Paso 4: Avance tras cumplir el delay (t0 + 6s) -> se reactiva a READY y se ejecuta (intento 2 -> falla)
    t_retry_1 = t0 + timedelta(seconds=6)
    engine.step(current_time=t_retry_1)
    assert attempts == 2
    assert engine.context.node_retries["flaky_task"] == 2
    # Con delay_seconds=5.0 y retries=1: delay = 5.0 * (2.0 ** 1) = 10.0s
    assert engine.context.node_states["flaky_task"] == NodeStatus.RETRYING
    retry_deadline_2 = engine.context.node_retry_after["flaky_task"]
    assert retry_deadline_2 == t_retry_1 + timedelta(seconds=10)

    # Paso 5: Avance tras cumplir el segundo delay (t_retry_1 + 11s) -> se ejecuta y tiene éxito
    t_retry_2 = t_retry_1 + timedelta(seconds=11)
    engine.step(current_time=t_retry_2)
    assert attempts == 3
    assert engine.context.node_states["flaky_task"] == NodeStatus.COMPLETED
    assert "flaky_task" not in engine.context.node_retry_after

    # Paso 6: Ejecuta End -> Workflow COMPLETED
    n_end = engine.step(current_time=t_retry_2)
    assert n_end == "end"
    assert engine.context.status == WorkflowStatus.COMPLETED


def test_retry_exhaustion_marks_workflow_failed():
    """Valida que agotar la cantidad máxima de reintentos marca el nodo y el workflow como FAILED."""
    wf = create_retry_workflow()

    def always_fails(inputs):
        raise ValueError("Error persistente irrecuperable")

    engine = WorkflowEngine(wf, task_handlers={"test_tool": always_fails})
    engine.start()
    engine.step()  # Start

    t0 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
    engine.step(current_time=t0)  # intento 1 (max_retries=2, retry 1 programado)
    assert engine.context.node_states["flaky_task"] == NodeStatus.RETRYING

    t1 = t0 + timedelta(seconds=6)
    engine.step(current_time=t1)  # intento 2 (retry 2 programado)
    assert engine.context.node_states["flaky_task"] == NodeStatus.RETRYING

    t2 = t1 + timedelta(seconds=15)
    engine.step(current_time=t2)  # intento 3 (retries agotados -> FAILED)
    assert engine.context.node_states["flaky_task"] == NodeStatus.FAILED
    assert engine.context.status == WorkflowStatus.FAILED
    assert "Error persistente irrecuperable" in (engine.context.error_message or "")


def test_async_agent_timeout_detection_and_retrying():
    """Valida la detección de timeout en un nodo AGENT en WAITING_RESULT y su reintento."""
    bus = AgentMessageBus()
    start_node = WorkflowNode(node_id="start", name="Start", node_type=NodeType.START)
    agent_node = WorkflowNode(
        node_id="agent_worker",
        name="Agent Worker",
        node_type=NodeType.AGENT,
        agent_id="code_agent",
        retry_policy=RetryPolicy(max_retries=1, delay_seconds=2.0),
        timeout_seconds=30,
    )
    end_node = WorkflowNode(node_id="end", name="End", node_type=NodeType.END)

    wf = WorkflowDefinition(
        workflow_id="wf_async_timeout",
        name="Async Timeout Test",
        nodes={"start": start_node, "agent_worker": agent_node, "end": end_node},
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="agent_worker"),
            WorkflowEdge(edge_id="e2", from_node="agent_worker", to_node="end"),
        ],
    )

    engine = WorkflowEngine(wf, agent_bus=bus)
    engine.start()
    engine.step()  # Start

    t0 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
    engine.step(current_time=t0)  # Despacha AGENT -> WAITING_RESULT
    assert engine.context.node_states["agent_worker"] == NodeStatus.WAITING_RESULT
    assert "agent_worker" in engine.context.node_started_at

    # Pasaron 20 segundos (< 30s timeout) -> no vence
    t_ok = t0 + timedelta(seconds=20)
    res = engine.check_scheduled_retries_and_timeouts(current_time=t_ok)
    assert res["timed_out"] == []
    assert engine.context.node_states["agent_worker"] == NodeStatus.WAITING_RESULT

    # Pasaron 35 segundos (> 30s timeout) -> vence y tiene max_retries=1 -> RETRYING
    t_timeout = t0 + timedelta(seconds=35)
    res2 = engine.check_scheduled_retries_and_timeouts(current_time=t_timeout)
    assert "agent_worker" in res2["timed_out"]
    assert engine.context.node_states["agent_worker"] == NodeStatus.RETRYING
    assert engine.context.node_retries["agent_worker"] == 1

    # Al vencer el backoff de 2s, se reactiva a READY
    t_wake = t_timeout + timedelta(seconds=3)
    res3 = engine.check_scheduled_retries_and_timeouts(current_time=t_wake)
    assert "agent_worker" in res3["ready_from_retry"]
    assert engine.context.node_states["agent_worker"] == NodeStatus.READY

    # Se vuelve a despachar
    engine.step(current_time=t_wake)
    assert engine.context.node_states["agent_worker"] == NodeStatus.WAITING_RESULT

    # Vence por segunda vez (> 30s tras t_wake) -> sin más reintentos -> TIMEOUT y workflow FAILED
    t_final_timeout = t_wake + timedelta(seconds=35)
    res4 = engine.check_scheduled_retries_and_timeouts(current_time=t_final_timeout)
    assert "agent_worker" in res4["timed_out"]
    assert engine.context.node_states["agent_worker"] == NodeStatus.TIMEOUT
    assert engine.context.status == WorkflowStatus.FAILED


def test_retry_node_manual_override_for_timeout_and_retrying():
    """Verifica que retry_node() reinicia inmediatamente nodos en TIMEOUT o RETRYING."""
    wf = create_retry_workflow()
    engine = WorkflowEngine(wf)
    engine.context.node_states["flaky_task"] = NodeStatus.TIMEOUT
    engine.context.status = WorkflowStatus.FAILED

    success = engine.retry_node("flaky_task")
    assert success is True
    assert engine.context.node_states["flaky_task"] == NodeStatus.READY
    assert engine.context.status == WorkflowStatus.RUNNING

    # Ahora en RETRYING
    engine.context.node_states["flaky_task"] = NodeStatus.RETRYING
    engine.context.node_retry_after["flaky_task"] = datetime.now(timezone.utc) + timedelta(minutes=5)
    success2 = engine.retry_node("flaky_task")
    assert success2 is True
    assert engine.context.node_states["flaky_task"] == NodeStatus.READY
    assert "flaky_task" not in engine.context.node_retry_after


def test_workflow_execution_and_context_temporal_serialization():
    """Verifica que node_started_at y node_retry_after se serializan y deserializan fielmente."""
    wf = create_retry_workflow()
    ctx = WorkflowExecutionContext(wf, execution_id="exec_temporal_test")
    t1 = datetime(2026, 10, 5, 14, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 5, 14, 0, 10, tzinfo=timezone.utc)

    ctx.node_started_at["flaky_task"] = t1
    ctx.node_retry_after["flaky_task"] = t2

    # to_execution
    execution = ctx.to_execution()
    assert execution.node_started_at["flaky_task"] == t1
    assert execution.node_retry_after["flaky_task"] == t2

    # to_dict / from_dict de WorkflowExecution
    d = execution.to_dict()
    assert isinstance(d["node_started_at"]["flaky_task"], str)
    restored_exec = WorkflowExecution.from_dict(d)
    assert restored_exec.node_started_at["flaky_task"] == t1
    assert restored_exec.node_retry_after["flaky_task"] == t2

    # from_execution a nuevo context
    new_ctx = WorkflowExecutionContext.from_execution(wf, restored_exec)
    assert new_ctx.node_started_at["flaky_task"] == t1
    assert new_ctx.node_retry_after["flaky_task"] == t2
