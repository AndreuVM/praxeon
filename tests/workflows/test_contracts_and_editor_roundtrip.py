"""
tests/workflows/test_contracts_and_editor_roundtrip.py

Pruebas exhaustivas para la Fase 2 (P1 · Alta):
Coherencia de Contratos Editor-API-Motor (IF/WHILE y DELEGATE):
1. Round-trip y preservación de configuración en WHILE (body_entry, exit_target, condition).
2. Normalización bidireccional de DELEGATE routing_mode vs strategy.
3. Semántica fail-closed en is_false / is_true con campos ausentes y NOT vacío.
4. Validación estructural estricta de grafos en validate_graph().
"""

import pytest
from praxeon.workflows.models import (
    NodeType,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowEdge,
    UIPosition,
    WorkflowStatus,
)
from praxeon.workflows.editor_service import WorkflowEditorService
from praxeon.persistence.sqlite_store import SqlitePersistenceStore
from praxeon.workflows.semantics import (
    AtomicCondition,
    CompoundCondition,
    LogicalOperator,
    parse_condition,
)
from praxeon.workflows.engine import WorkflowEngine


# ==============================================================================
# 1. Semántica Fail-Closed y rechazo de NOT vacío
# ==============================================================================

def test_atomic_condition_missing_field_fails_closed():
    """Valida que is_false e is_true sobre un campo ausente o None retornen False."""
    context = {"outputs": {}}

    # Campo inexistente con is_false
    cond_false = AtomicCondition(
        field="outputs.not_found.flag",
        operator="is_false",
    )
    # Anteriormente esto devolvía True (bool(None) is False -> True)
    # Ahora debe retornar False (fail-closed)
    assert cond_false.evaluate(context) is False

    # Campo inexistente con is_true
    cond_true = AtomicCondition(
        field="outputs.not_found.flag",
        operator="is_true",
    )
    assert cond_true.evaluate(context) is False

    # Campo existente explícitamente False
    context_with_false = {"outputs": {"flag": False}}
    cond_existing_false = AtomicCondition(
        field="outputs.flag",
        operator="is_false",
    )
    assert cond_existing_false.evaluate(context_with_false) is True

    # Campo existente explícitamente True
    context_with_true = {"outputs": {"flag": True}}
    cond_existing_true = AtomicCondition(
        field="outputs.flag",
        operator="is_true",
    )
    assert cond_existing_true.evaluate(context_with_true) is True


def test_compound_condition_empty_not_fails_closed_and_rejected():
    """Valida que un NOT vacío falle cerrado y sea rechazado en parse_condition."""
    # Instanciación directa: CompoundCondition con NOT y lista vacía
    compound_empty_not = CompoundCondition(
        logical_op=LogicalOperator.NOT,
        conditions=[],
    )
    assert compound_empty_not.evaluate({"outputs": {}}) is False

    # parse_condition con NOT vacío debe ser rechazado (devolver None)
    parsed = parse_condition({"not": []})
    assert parsed is None

    # parse_condition con NOT válido
    parsed_valid = parse_condition({
        "not": [
            {"field": "outputs.status", "operator": "==", "expected_value": "FAILED"}
        ]
    })
    assert parsed_valid is not None
    assert parsed_valid.evaluate({"outputs": {"status": "SUCCESS"}}) is True
    assert parsed_valid.evaluate({"outputs": {"status": "FAILED"}}) is False


# ==============================================================================
# 2. Validación Estructural de Grafos (validate_graph)
# ==============================================================================

def test_validate_graph_checks_while_body_and_exit_references():
    """Valida que validate_graph compruebe la existencia de body_entry y exit_target en WHILE."""
    start = WorkflowNode(node_id="start", name="Start", node_type=NodeType.START)
    body = WorkflowNode(node_id="task_body", name="Body", node_type=NodeType.TASK)
    exit_node = WorkflowNode(node_id="task_exit", name="Exit", node_type=NodeType.TASK)
    end = WorkflowNode(node_id="end", name="End", node_type=NodeType.END)

    # 1. WHILE con referencias inexistentes
    while_invalid = WorkflowNode(
        node_id="loop_1",
        name="While Loop",
        node_type=NodeType.WHILE,
        control_config={
            "body_entry": "non_existent_body",
            "exit_target": "non_existent_exit",
            "max_iterations": 5,
        },
    )

    wf_invalid = WorkflowDefinition(
        workflow_id="wf_val_1",
        name="Invalid While Flow",
        nodes={
            "start": start,
            "loop_1": while_invalid,
            "task_body": body,
            "task_exit": exit_node,
            "end": end,
        },
        edges=[],
    )
    errors = wf_invalid.validate_graph()
    assert any("referencia un 'body_entry' inexistente: 'non_existent_body'" in err for err in errors)
    assert any("referencia un 'exit_target' inexistente: 'non_existent_exit'" in err for err in errors)

    # 2. WHILE con referencias válidas
    while_valid = WorkflowNode(
        node_id="loop_1",
        name="While Loop",
        node_type=NodeType.WHILE,
        control_config={
            "body_entry": "task_body",
            "exit_target": "task_exit",
            "max_iterations": 5,
        },
    )
    wf_valid = WorkflowDefinition(
        workflow_id="wf_val_2",
        name="Valid While Flow",
        nodes={
            "start": start,
            "loop_1": while_valid,
            "task_body": body,
            "task_exit": exit_node,
            "end": end,
        },
        edges=[],
    )
    errors_valid = wf_valid.validate_graph()
    # No debe haber errores relativos a body_entry o exit_target
    assert not any("body_entry" in err for err in errors_valid)
    assert not any("exit_target" in err for err in errors_valid)


def test_validate_graph_checks_delegate_routing_mode():
    """Valida que validate_graph rechace routing_mode inválido en nodos DELEGATE."""
    start = WorkflowNode(node_id="start", name="Start", node_type=NodeType.START)
    end = WorkflowNode(node_id="end", name="End", node_type=NodeType.END)
    delegate_invalid = WorkflowNode(
        node_id="del_1",
        name="Delegate",
        node_type=NodeType.DELEGATE,
        control_config={
            "routing_mode": "UNKNOWN_STRATEGY",
        },
    )

    wf = WorkflowDefinition(
        workflow_id="wf_del_val",
        name="Delegate Test",
        nodes={"start": start, "del_1": delegate_invalid, "end": end},
        edges=[],
    )
    errors = wf.validate_graph()
    assert any("tiene un modo de enrutamiento inválido: 'UNKNOWN_STRATEGY'" in err for err in errors)

    # Modo válido AUTOMATIC o MANUAL
    delegate_invalid.control_config["routing_mode"] = "AUTOMATIC"
    errors_ok = wf.validate_graph()
    assert not any("modo de enrutamiento inválido" in err for err in errors_ok)


# ==============================================================================
# 3. Normalización DELEGATE y Round-trip en WorkflowEditorService
# ==============================================================================

def test_editor_service_delegate_normalization():
    """Verifica que WorkflowEditorService normalice strategy y routing_mode bidireccionalmente."""
    editor = WorkflowEditorService()
    wf = editor.create_workflow(name="Delegate Norm Flow")

    # A) Añadir con strategy="AUTOMATIC"
    node1 = editor.add_node(
        workflow_id=wf.workflow_id,
        name="Router Alpha",
        node_type=NodeType.DELEGATE,
        control_config={"strategy": "AUTOMATIC", "candidate_agents": ["agent_a", "agent_b"]},
    )
    assert node1.control_config["routing_mode"] == "AUTOMATIC"
    assert node1.control_config["strategy"] == "AUTOMATIC"

    # B) Actualizar con routing_mode="MANUAL"
    node1_updated = editor.update_node(
        workflow_id=wf.workflow_id,
        node_id=node1.node_id,
        control_config={"routing_mode": "MANUAL"},
    )
    assert node1_updated.control_config["routing_mode"] == "MANUAL"
    assert node1_updated.control_config["strategy"] == "MANUAL"
    # El merge conservativo debe haber preservado candidate_agents
    assert node1_updated.control_config["candidate_agents"] == ["agent_a", "agent_b"]


def test_editor_service_while_and_if_roundtrip_persistence(tmp_path):
    """Verifica preservación de contratos en WHILE e IF a través del ciclo completo de edición y SQLite."""
    db_file = str(tmp_path / "contracts_persistence.db")
    store = SqlitePersistenceStore(db_path=db_file)

    editor1 = WorkflowEditorService(persistence_store=store)
    wf = editor1.create_workflow(name="Full Contracts Flow")
    wid = wf.workflow_id

    # 1. Crear nodo WHILE completo
    while_node = editor1.add_node(
        workflow_id=wid,
        name="Review Loop",
        node_type=NodeType.WHILE,
        control_config={
            "max_iterations": 5,
            "on_limit": "ABORT",
            "body_entry": "step_review",
            "exit_target": "step_finish",
            "condition": {"field": "outputs.reviewed", "operator": "==", "expected_value": True},
        },
    )
    assert while_node.control_config["body_entry"] == "step_review"
    assert while_node.control_config["exit_target"] == "step_finish"
    assert while_node.control_config["max_iterations"] == 5

    # 2. Actualización parcial del nodo WHILE (ej. solo cambio de nombre o una propiedad)
    while_updated = editor1.update_node(
        workflow_id=wid,
        node_id=while_node.node_id,
        name="Review Loop Renamed",
        control_config={"max_iterations": 8},
    )
    # Conservación de body_entry, exit_target, on_limit y condition
    assert while_updated.control_config["max_iterations"] == 8
    assert while_updated.control_config["body_entry"] == "step_review"
    assert while_updated.control_config["exit_target"] == "step_finish"
    assert while_updated.control_config["on_limit"] == "ABORT"
    assert while_updated.control_config["condition"]["field"] == "outputs.reviewed"

    # 3. Crear nodo IF con true_edge y false_edge
    if_node = editor1.add_node(
        workflow_id=wid,
        name="Decision Gate",
        node_type=NodeType.IF,
        control_config={
            "true_edge": "edge_approve",
            "false_edge": "edge_reject",
            "condition": {"field": "outputs.score", "operator": ">", "expected_value": 75},
        },
    )
    assert if_node.control_config["true_edge"] == "edge_approve"
    assert if_node.control_config["false_edge"] == "edge_reject"

    # 4. Simular reinicio del servicio recargando desde SQLite
    editor2 = WorkflowEditorService(persistence_store=store)
    wf_restored = editor2.get_workflow(wid)
    assert wf_restored is not None

    restored_while = wf_restored.nodes[while_node.node_id]
    assert restored_while.control_config["body_entry"] == "step_review"
    assert restored_while.control_config["exit_target"] == "step_finish"
    assert restored_while.control_config["max_iterations"] == 8

    restored_if = wf_restored.nodes[if_node.node_id]
    assert restored_if.control_config["true_edge"] == "edge_approve"
    assert restored_if.control_config["false_edge"] == "edge_reject"


def test_delegate_automatic_routing_execution():
    """Verifica que el motor ejecute correctamente un nodo DELEGATE configurado con routing_mode='AUTOMATIC'."""
    start = WorkflowNode(node_id="start", name="Start", node_type=NodeType.START)
    delegate = WorkflowNode(
        node_id="delegate_node",
        name="Auto Delegator",
        node_type=NodeType.DELEGATE,
        control_config={
            "routing_mode": "AUTOMATIC",
            "candidate_agents": ["expert_agent_1", "expert_agent_2"],
        },
    )
    end = WorkflowNode(node_id="end", name="End", node_type=NodeType.END)

    e1 = WorkflowEdge(edge_id="e1", from_node="start", to_node="delegate_node")
    e2 = WorkflowEdge(edge_id="e2", from_node="delegate_node", to_node="end")

    wf = WorkflowDefinition(
        workflow_id="wf_delegate_exec",
        name="Delegate Auto Flow",
        nodes={"start": start, "delegate_node": delegate, "end": end},
        edges=[e1, e2],
    )

    engine = WorkflowEngine(workflow=wf, allow_synthetic_fallback=True)
    ctx = engine.run_to_completion()
    assert ctx.status == WorkflowStatus.COMPLETED
    del_out = ctx.node_outputs.get("delegate_node", {})
    assert del_out.get("delegated_to") == "expert_agent_1"
    assert del_out.get("routing_mode") == "AUTOMATIC"
    assert del_out.get("status") == "completed"
