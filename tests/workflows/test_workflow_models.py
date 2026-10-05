"""Pruebas unitarias para el Modelo de Datos y Validación de Workflows (Fase 6 - F6-01).

Valida:
- Tipado, inmutabilidad y ciclo de vida de WorkflowNode y WorkflowEdge.
- Evaluación segura de condiciones lógicas en EdgeCondition.
- Validación topológica del grafo: START único, END presente, conectividad, alcanzabilidad.
- Detección de ciclos dependientes (Kahn DAG validation).
- Ordenación topológica para resolución de dependencias.
- Portabilidad y roundtrip en JSON y YAML.
"""

import pytest

from praxeon.workflows import (
    EdgeCondition,
    NodeStatus,
    NodeType,
    RetryPolicy,
    UIPosition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)


def test_node_creation_and_immutability():
    """Valida la creación correcta de nodos y la actualización inmutable de estado."""
    node = WorkflowNode(
        node_id="node_task_01",
        name="Analyze Codebase",
        node_type=NodeType.AGENT,
        agent_id="ag_dev_01",
        inputs={"repo_path": "/workspace"},
        retry_policy=RetryPolicy(max_retries=2, delay_seconds=0.5),
        position=UIPosition(x=150.0, y=300.0),
    )

    assert node.node_id == "node_task_01"
    assert node.node_type == NodeType.AGENT
    assert node.status == NodeStatus.PENDING
    assert node.retry_policy.max_retries == 2
    assert node.position.x == 150.0

    # Inmutabilidad: with_status genera una nueva instancia
    updated = node.with_status(NodeStatus.COMPLETED, outputs={"summary": "All tests passed"})
    assert updated.status == NodeStatus.COMPLETED
    assert updated.outputs["summary"] == "All tests passed"
    assert node.status == NodeStatus.PENDING  # El original se preserva


def test_edge_condition_evaluation():
    """Valida la evaluación segura de condiciones lógicas sin eval() arbitrario."""
    # Igualdad
    c1 = EdgeCondition(field="status", operator="==", expected_value="success")
    assert c1.evaluate({"status": "success"}) is True
    assert c1.evaluate({"status": "failure"}) is False

    # Comparación numérica anidada
    c2 = EdgeCondition(field="output.confidence", operator=">=", expected_value=0.85)
    assert c2.evaluate({"output": {"confidence": 0.9}}) is True
    assert c2.evaluate({"output": {"confidence": 0.75}}) is False
    assert c2.evaluate({"output": {}}) is False

    # Pertenencia 'in'
    c3 = EdgeCondition(field="risk_level", operator="in", expected_value=["low", "medium"])
    assert c3.evaluate({"risk_level": "low"}) is True
    assert c3.evaluate({"risk_level": "critical"}) is False

    # Booleano 'is_true'
    c4 = EdgeCondition(field="metadata.is_approved", operator="is_true")
    assert c4.evaluate({"metadata": {"is_approved": True}}) is True
    assert c4.evaluate({"metadata": {"is_approved": False}}) is False


def test_valid_dag_workflow_validation_and_topological_sort():
    """Valida que un flujo DAG bien formado pase la validación y genere un orden topológico válido."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "research": WorkflowNode(node_id="research", name="Research", node_type=NodeType.AGENT, agent_id="ag_res_01"),
        "dev": WorkflowNode(node_id="dev", name="Develop", node_type=NodeType.AGENT, agent_id="ag_dev_01"),
        "review": WorkflowNode(node_id="review", name="Code Review", node_type=NodeType.AGENT, agent_id="ag_rev_01"),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }

    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="research"),
        WorkflowEdge(edge_id="e2", from_node="research", to_node="dev"),
        WorkflowEdge(edge_id="e3", from_node="dev", to_node="review"),
        WorkflowEdge(edge_id="e4", from_node="review", to_node="end"),
    ]

    wf = WorkflowDefinition(
        workflow_id="wf_linear_01",
        name="Linear CI/CD Pipeline",
        nodes=nodes,
        edges=edges,
    )

    errors = wf.validate_graph()
    assert len(errors) == 0

    order = wf.topological_sort()
    assert order == ["start", "research", "dev", "review", "end"]


def test_workflow_validation_catches_missing_start_or_end():
    """Valida la detección de flujos sin START o sin END."""
    # Sin nodo START
    wf_no_start = WorkflowDefinition(
        workflow_id="wf_no_start",
        name="Broken Flow",
        nodes={"end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END)},
        edges=[],
    )
    errors = wf_no_start.validate_graph()
    assert any("carece de nodo de inicio" in e for e in errors)

    # Sin nodo END
    wf_no_end = WorkflowDefinition(
        workflow_id="wf_no_end",
        name="Broken Flow",
        nodes={"start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START)},
        edges=[],
    )
    errors2 = wf_no_end.validate_graph()
    assert any("carece de al menos un nodo terminal" in e for e in errors2)


def test_workflow_validation_catches_unreachable_orphan_nodes():
    """Valida la detección de nodos aislados que no son alcanzables desde START."""
    wf = WorkflowDefinition(
        workflow_id="wf_orphan",
        name="Flow with Orphan",
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "step1": WorkflowNode(node_id="step1", name="Step 1", node_type=NodeType.TASK),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
            "isolated": WorkflowNode(node_id="isolated", name="Orphan", node_type=NodeType.TASK),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="step1"),
            WorkflowEdge(edge_id="e2", from_node="step1", to_node="end"),
        ],
    )

    errors = wf.validate_graph()
    assert any("Nodos inalcanzables desde START" in e for e in errors)
    assert any("isolated" in e for e in errors)


def test_workflow_validation_catches_cycles():
    """Valida que la ordenación y validación detecten ciclos dirigidos (no acíclicos)."""
    wf = WorkflowDefinition(
        workflow_id="wf_cycle",
        name="Cyclic Flow",
        nodes={
            "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
            "step_a": WorkflowNode(node_id="step_a", name="Step A", node_type=NodeType.TASK),
            "step_b": WorkflowNode(node_id="step_b", name="Step B", node_type=NodeType.TASK),
            "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
        },
        edges=[
            WorkflowEdge(edge_id="e1", from_node="start", to_node="step_a"),
            WorkflowEdge(edge_id="e2", from_node="step_a", to_node="step_b"),
            WorkflowEdge(edge_id="e3", from_node="step_b", to_node="step_a"),  # Ciclo A <-> B
            WorkflowEdge(edge_id="e4", from_node="step_b", to_node="end"),
        ],
    )

    errors = wf.validate_graph()
    assert any("dependencias circulares" in e for e in errors)

    with pytest.raises(ValueError, match="No se puede ordenar un grafo inválido"):
        wf.topological_sort()


def test_workflow_serialization_roundtrip_json_and_yaml():
    """Valida la exportación e importación fiel en JSON y YAML."""
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "task_1": WorkflowNode(node_id="task_1", name="Compile", node_type=NodeType.TASK, tool_name="run_build"),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="task_1"),
        WorkflowEdge(edge_id="e2", from_node="task_1", to_node="end"),
    ]
    wf = WorkflowDefinition(
        workflow_id="wf_portability",
        name="Portability Test Flow",
        description="Verifies JSON and YAML roundtrip fidelity",
        nodes=nodes,
        edges=edges,
        variables={"build_env": "production"},
    )

    # JSON Roundtrip
    dict_repr = wf.to_dict()
    wf_from_dict = WorkflowDefinition.from_dict(dict_repr)
    assert wf_from_dict.workflow_id == wf.workflow_id
    assert len(wf_from_dict.nodes) == 3
    assert wf_from_dict.variables["build_env"] == "production"

    # YAML Roundtrip
    yaml_str = wf.to_yaml()
    assert "build_env: production" in yaml_str
    wf_from_yaml = WorkflowDefinition.from_yaml(yaml_str)
    assert wf_from_yaml.workflow_id == wf.workflow_id
    assert len(wf_from_yaml.edges) == 2
    assert wf_from_yaml.nodes["task_1"].tool_name == "run_build"
