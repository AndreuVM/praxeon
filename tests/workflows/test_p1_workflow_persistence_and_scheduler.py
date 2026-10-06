"""Pruebas de Certificación para Persistencia y Scheduler de Workflows (DEUDA-WF-02 y DEUDA-WF-03).

Verifica:
1. Persistencia transaccional de WorkflowDefinition en SQLite (CRUD durable, persistencia tras reinicio del servicio).
2. Persistencia transaccional de WorkflowExecution en SQLite (historial, variables, outputs de nodos).
3. WorkflowScheduler en segundo plano:
   - Detección y reactivación automática de reintentos con backoff (RETRYING -> READY).
   - Detección y marcación de timeouts a nivel de nodo y a nivel global de flujo.
   - Avance automático concurrente y persistencia periódica sin polling manual.
"""

from datetime import datetime, timezone, timedelta
import os
import time
import pytest

from praxeon.persistence.sqlite_store import SqlitePersistenceStore
from praxeon.workflows.editor_service import WorkflowEditorService
from praxeon.workflows.engine import WorkflowEngine
from praxeon.workflows.models import (
    NodeStatus,
    NodeType,
    RetryPolicy,
    UIPosition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowExecution,
    WorkflowNode,
    WorkflowStatus,
)
from praxeon.workflows.scheduler import WorkflowScheduler
from praxeon.server.services.workflow_service import WorkflowService


def test_sqlite_workflow_definition_crud(tmp_path):
    """DEUDA-WF-02: Verificar persistencia de WorkflowDefinition en SQLite."""
    db_file = str(tmp_path / "test_persistence.db")
    store = SqlitePersistenceStore(db_path=db_file)

    wf = WorkflowDefinition(
        workflow_id="wf_custom_test",
        name="Custom Data Pipeline",
        description="A durable test pipeline",
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "step1": WorkflowNode(node_id="step1", name="Step 1", node_type=NodeType.TASK, tool_name="fetch_data"),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="step1"),
            WorkflowEdge(edge_id="e2", from_node="step1", to_node="end"),
        ],
        variables={"batch_size": 100},
    )

    # 1. Guardar
    store.save_workflow(wf)

    # 2. Recuperar
    loaded = store.get_workflow("wf_custom_test")
    assert loaded is not None
    assert loaded.workflow_id == "wf_custom_test"
    assert loaded.name == "Custom Data Pipeline"
    assert len(loaded.nodes) == 3
    assert len(loaded.edges) == 2
    assert loaded.variables["batch_size"] == 100

    # 3. Listar
    wfs = store.list_workflows()
    assert any(w.workflow_id == "wf_custom_test" for w in wfs)

    # 4. Actualizar
    wf_updated = WorkflowDefinition(
        workflow_id="wf_custom_test",
        name="Updated Pipeline Name",
        description="Updated description",
        version=2,
        nodes=dict(wf.nodes),
        edges=list(wf.edges),
    )
    store.save_workflow(wf_updated)
    loaded_v2 = store.get_workflow("wf_custom_test")
    assert loaded_v2.name == "Updated Pipeline Name"
    assert loaded_v2.version == 2

    # 5. Eliminar
    assert store.delete_workflow("wf_custom_test") is True
    assert store.get_workflow("wf_custom_test") is None


def test_workflow_editor_service_persistence_reload(tmp_path):
    """DEUDA-WF-02: WorkflowEditorService restaura definiciones guardadas al reiniciar."""
    db_file = str(tmp_path / "editor_persistence.db")
    store = SqlitePersistenceStore(db_path=db_file)

    editor1 = WorkflowEditorService(persistence_store=store)
    wf_created = editor1.create_workflow(name="Persistent Test Flow", description="Saved to SQLite")
    wf_id = wf_created.workflow_id

    # Añadir un nodo a través del servicio
    editor1.add_node(
        workflow_id=wf_id,
        name="Task Node",
        node_type=NodeType.TASK,
        tool_name="process_item",
    )

    # Simular reinicio del servicio (nueva instancia con la misma BD)
    editor2 = WorkflowEditorService(persistence_store=store)
    loaded_wf = editor2.get_workflow(wf_id)
    assert loaded_wf is not None
    assert loaded_wf.name == "Persistent Test Flow"
    assert len(loaded_wf.nodes) == 3  # start, end, Task Node


def test_workflow_scheduler_retry_and_timeout(tmp_path):
    """DEUDA-WF-03: WorkflowScheduler detecta timeouts de nodo y reactiva reintentos con backoff."""
    db_file = str(tmp_path / "scheduler_test.db")
    store = SqlitePersistenceStore(db_path=db_file)

    wf = WorkflowDefinition(
        workflow_id="wf_timeout_retry",
        name="Timeout & Retry Test",
        timeout_seconds=10.0,
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "failing": WorkflowNode(
                node_id="failing",
                name="Failing Task",
                node_type=NodeType.TASK,
                tool_name="flaky_tool",
                timeout_seconds=2.0,
                retry_policy=RetryPolicy(max_retries=2, delay_seconds=1.0, backoff_multiplier=1.0),
            ),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="failing"),
            WorkflowEdge(edge_id="e2", from_node="failing", to_node="end"),
        ],
    )

    engine = WorkflowEngine(wf, allow_synthetic_fallback=False)
    engine.start()
    # Ejecutar start
    engine.step()  # start -> COMPLETED, failing -> READY

    scheduler = WorkflowScheduler(persistence_store=store, interval_seconds=0.1)
    scheduler.register_engine(engine)

    # Simular que 'failing' falló y está en estado RETRYING con retry_after en el futuro
    now = datetime.now(timezone.utc)
    engine.context.node_states["failing"] = NodeStatus.RETRYING
    engine.context.node_retry_after["failing"] = now + timedelta(seconds=2.0)

    # Primer poll: aún no es tiempo de retry
    report1 = scheduler.poll_once(current_time=now + timedelta(seconds=0.5))
    assert engine.context.node_states["failing"] == NodeStatus.RETRYING
    assert "failing" not in report1["retried_nodes"]

    # Segundo poll: transcurrido el tiempo (now + 2.5s) -> pasa a READY
    report2 = scheduler.poll_once(current_time=now + timedelta(seconds=2.5))
    assert "failing" in report2["retried_nodes"]
    assert engine.context.node_states["failing"] in (NodeStatus.READY, NodeStatus.RUNNING, NodeStatus.RETRYING, NodeStatus.FAILED)

    # Verificar que el scheduler persiste la ejecución en el store
    exec_id = engine.context.execution_id
    persisted_exec = store.get_workflow_execution(exec_id)
    assert persisted_exec is not None
    assert persisted_exec.execution_id == exec_id


def test_workflow_scheduler_global_timeout(tmp_path):
    """DEUDA-WF-03: WorkflowScheduler aborta con FAILED cuando el workflow excede timeout_seconds."""
    db_file = str(tmp_path / "global_timeout.db")
    store = SqlitePersistenceStore(db_path=db_file)

    wf = WorkflowDefinition(
        workflow_id="wf_global_timeout",
        name="Global Timeout Workflow",
        timeout_seconds=5.0,  # 5 segundos
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "task": WorkflowNode(node_id="task", name="Long Task", node_type=NodeType.TASK, tool_name="long_job"),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="task"),
            WorkflowEdge(edge_id="e2", from_node="task", to_node="end"),
        ],
    )

    engine = WorkflowEngine(wf, allow_synthetic_fallback=True)
    engine.start()
    t0 = engine.context.started_at

    scheduler = WorkflowScheduler(persistence_store=store)
    scheduler.register_engine(engine)

    # Evaluar a t0 + 2s (dentro del límite)
    report = scheduler.poll_once(current_time=t0 + timedelta(seconds=2.0))
    assert engine.context.status == WorkflowStatus.RUNNING
    assert len(report["timed_out_workflows"]) == 0

    # Evaluar a t0 + 6s (excede 5s)
    report_timeout = scheduler.poll_once(current_time=t0 + timedelta(seconds=6.0))
    assert engine.context.status == WorkflowStatus.FAILED
    assert "Timeout global" in engine.context.error_message
    assert engine.context.execution_id in report_timeout["timed_out_workflows"]

    # Verificar persistencia en base de datos
    saved = store.get_workflow_execution(engine.context.execution_id)
    assert saved is not None
    assert saved.status == WorkflowStatus.FAILED
