"""Pruebas unitarias para la separación arquitectónica de WorkflowDefinition y WorkflowExecution (P1-WORKFLOW).

Valida:
- Desacoplamiento entre la especificación estática/inmutable (WorkflowDefinition) y la entidad dinámica (WorkflowExecution).
- Capacidad de instanciar múltiples ejecuciones concurrentes del mismo workflow sin colisión de estado.
- Aislamiento completo de variables, outputs, estados de nodo y checkpoints entre ejecuciones.
- Vinculación y conmutación de ejecuciones en WorkflowEngine mediante bind_execution().
- Métodos auxiliares de ciclo de vida (is_active, is_terminal) y serialización to_dict / from_dict.
"""

import pytest

from praxeon.workflows import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowEngine,
    WorkflowExecution,
    WorkflowNode,
    WorkflowStatus,
)


@pytest.fixture
def sample_definition():
    """Genera un WorkflowDefinition canónico simple con nodos START -> TASK -> END."""
    start = WorkflowNode(node_id="start", name="Start", node_type=NodeType.START)
    task1 = WorkflowNode(
        node_id="process_data",
        name="Process Data",
        node_type=NodeType.TASK,
        inputs={"factor": 2},
    )
    end = WorkflowNode(node_id="end", name="End", node_type=NodeType.END)

    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="process_data"),
        WorkflowEdge(edge_id="e2", from_node="process_data", to_node="end"),
    ]

    return WorkflowDefinition(
        workflow_id="wf_data_pipeline",
        name="Data Pipeline Workflow",
        nodes={"start": start, "process_data": task1, "end": end},
        edges=edges,
        variables={"base_value": 10},
    )


def test_workflow_definition_create_execution(sample_definition):
    """Valida la creación de una entidad formal WorkflowExecution a partir de WorkflowDefinition."""
    exec1 = sample_definition.create_execution(initial_variables={"tenant": "acme"})

    assert isinstance(exec1, WorkflowExecution)
    assert exec1.workflow_id == "wf_data_pipeline"
    assert exec1.execution_id.startswith("exec_")
    assert exec1.status == WorkflowStatus.IDLE
    assert exec1.variables["base_value"] == 10
    assert exec1.variables["tenant"] == "acme"
    assert exec1.node_states["start"] == NodeStatus.PENDING
    assert exec1.node_states["process_data"] == NodeStatus.PENDING
    assert not exec1.is_active()
    assert not exec1.is_terminal()


def test_concurrent_independent_workflow_executions(sample_definition):
    """Valida que múltiples ejecuciones del mismo workflow operen concurrentemente sin interferencia ni fuga de estado."""
    # Handlers para cómputo real
    handlers = {
        "process_data": lambda inp: {"computed": inp.get("factor", 1) * inp.get("base_value", 1)}
    }

    # Instancia 1
    engine1 = WorkflowEngine(sample_definition, task_handlers=handlers)
    # Instancia 2 compartiendo la misma definición
    engine2 = WorkflowEngine(sample_definition, task_handlers=handlers)

    # Iniciar con variables diferentes
    ctx1 = engine1.start(initial_variables={"base_value": 100})
    ctx2 = engine2.start(initial_variables={"base_value": 500})

    assert engine1.context.execution_id != engine2.context.execution_id
    assert engine1.context.variables["base_value"] == 100
    assert engine2.context.variables["base_value"] == 500

    # Ejecutar engine1 hasta completar
    engine1.run_to_completion()
    exec1 = engine1.get_execution()

    assert exec1.status == WorkflowStatus.COMPLETED
    assert exec1.is_terminal() is True
    assert exec1.node_states["process_data"] == NodeStatus.COMPLETED
    assert exec1.node_outputs["process_data"]["computed"] == 200

    # Verificar que engine2 NO fue alterado por la ejecución de engine1
    exec2_before = engine2.get_execution()
    assert exec2_before.status == WorkflowStatus.RUNNING
    assert exec2_before.node_states["process_data"] == NodeStatus.PENDING  # start finalizó pero process_data no ejecutó aún
    assert "process_data" not in exec2_before.node_outputs

    # Ahora ejecutar engine2
    engine2.run_to_completion()
    exec2 = engine2.get_execution()

    assert exec2.status == WorkflowStatus.COMPLETED
    assert exec2.node_outputs["process_data"]["computed"] == 1000

    # Confirmar que la definición inmutable original no fue contaminada
    assert sample_definition.variables["base_value"] == 10
    assert "computed" not in sample_definition.nodes["process_data"].outputs


def test_engine_bind_execution(sample_definition):
    """Valida la conmutación dinámica de contextos de ejecución en WorkflowEngine."""
    exec_entity = sample_definition.create_execution(
        execution_id="exec_custom_99",
        initial_variables={"counter": 42},
    )

    engine = WorkflowEngine(sample_definition)
    assert engine.context.execution_id != "exec_custom_99"

    engine.bind_execution(exec_entity)
    assert engine.context.execution_id == "exec_custom_99"
    assert engine.context.variables["counter"] == 42
    assert engine.get_execution().execution_id == "exec_custom_99"


def test_workflow_execution_serialization_roundtrip(sample_definition):
    """Valida la serialización y deserialización a diccionario de WorkflowExecution."""
    exec_entity = sample_definition.create_execution(
        execution_id="exec_serial_01",
        initial_variables={"env": "staging"},
    )
    exec_entity.status = WorkflowStatus.PAUSED
    exec_entity.node_states["start"] = NodeStatus.COMPLETED
    exec_entity.node_outputs["start"] = {"triggered_by": "api"}

    dump = exec_entity.to_dict()
    assert dump["execution_id"] == "exec_serial_01"
    assert dump["status"] == "PAUSED"
    assert dump["node_states"]["start"] == "COMPLETED"

    restored = WorkflowExecution.from_dict(dump)
    assert restored.execution_id == "exec_serial_01"
    assert restored.status == WorkflowStatus.PAUSED
    assert restored.node_states["start"] == NodeStatus.COMPLETED
    assert restored.node_outputs["start"]["triggered_by"] == "api"
    assert restored.is_active() is True
    assert restored.is_terminal() is False
