"""Un campo de condición mal escrito (con el valor pegado dentro) se rechaza al validar, en vez de dejar al agente en un bucle."""
from praxeon.workflows.models import (
    NodeType, WorkflowDefinition, WorkflowEdge, WorkflowNode, invalid_condition_field,
)


def _wf(field: str) -> WorkflowDefinition:
    nodes = {
        "start": WorkflowNode(node_id="start", name="Start", node_type=NodeType.START),
        "rev": WorkflowNode(node_id="rev", name="Rev", node_type=NodeType.AGENT, agent_id="a"),
        "loop": WorkflowNode(
            node_id="loop", name="Bucle", node_type=NodeType.WHILE,
            control_config={"max_iterations": 3, "on_limit": "ABORT", "exit_target": "end", "body_entry": "rev",
                            "condition": {"field": field, "operator": "is_true", "expected_value": None}},
        ),
        "end": WorkflowNode(node_id="end", name="End", node_type=NodeType.END),
    }
    edges = [
        WorkflowEdge(edge_id="e1", from_node="start", to_node="rev"),
        WorkflowEdge(edge_id="e2", from_node="rev", to_node="loop"),
        WorkflowEdge(edge_id="e3", from_node="loop", to_node="end"),
        WorkflowEdge(edge_id="e4", from_node="loop", to_node="rev"),
    ]
    return WorkflowDefinition(workflow_id="w", name="w", nodes=nodes, edges=edges)


def test_field_with_pasted_value_is_flagged():
    assert invalid_condition_field('outputs.rev."approved": true')
    assert invalid_condition_field("outputs.rev.approved == true")


def test_clean_paths_and_constants_are_valid():
    for ok in ("outputs.node_d8dcb869.approved", "loop.iteration", "output.status", "_const_true", "variables.max-retries"):
        assert invalid_condition_field(ok) is None


def test_graph_validation_reports_the_bad_condition():
    errors = _wf('outputs.rev."approved": true').validate_graph()
    assert any("no es una ruta válida" in e for e in errors)
    assert not any("no es una ruta válida" in e for e in _wf("outputs.rev.approved").validate_graph())
