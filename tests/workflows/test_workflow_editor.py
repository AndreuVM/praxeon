"""Pruebas unitarias para el Servicio y API del Editor Visual Drag & Drop (Fase 6 - F6-04).

Valida:
- WorkflowEditorService: CRUD, drag & drop de coordenadas, cableado de aristas.
- Simulación interactiva paso a paso, ejecución completa y backtrack determinista desde el editor.
- Endpoints REST de FastAPI para workflows (/v1/workflows).
- Servido de la interfaz visual web interactiva (/v1/workflows/editor/ui).
"""

import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app
from praxeon.workflows import (
    NodeType,
    UIPosition,
    WorkflowEditorService,
)


@pytest.fixture
def editor_service():
    """Servicio de editor aislado para pruebas unitarias."""
    return WorkflowEditorService()


@pytest.fixture
def api_client():
    """Cliente HTTP de prueba para la API FastAPI de PRAXEON."""
    app = create_app(profile="dev")
    return TestClient(app)


def test_editor_service_workflow_lifecycle_and_drag_drop(editor_service):
    """Valida la creación, reposicionamiento drag & drop de nodos y cableado."""
    # 1. Crear nuevo flujo
    wf = editor_service.create_workflow(name="Custom Flow", description="Drag drop test flow")
    assert wf.name == "Custom Flow"
    assert "start" in wf.nodes
    assert "end" in wf.nodes

    # 2. Añadir nodo TASK intermedio
    task_node = editor_service.add_node(
        workflow_id=wf.workflow_id,
        name="Security Scanner",
        node_type=NodeType.TASK,
        position=UIPosition(x=250.0, y=180.0),
        tool_name="security_scan",
    )
    assert task_node.position.x == 250.0
    assert task_node.position.y == 180.0

    # 3. Simular arrastre en el lienzo (drag & drop)
    updated_node = editor_service.update_node_position(
        workflow_id=wf.workflow_id,
        node_id=task_node.node_id,
        x=420.0,
        y=310.0,
    )
    assert updated_node.position.x == 420.0
    assert updated_node.position.y == 310.0

    # 4. Conectar nodos
    edge = editor_service.connect_nodes(
        workflow_id=wf.workflow_id,
        from_node="start",
        to_node=task_node.node_id,
        label="Trigger Scan",
    )
    assert edge.from_node == "start"
    assert edge.to_node == task_node.node_id

    # 5. Desconectar arista
    removed_edge = editor_service.disconnect_nodes(wf.workflow_id, edge.edge_id)
    assert removed_edge is True

    # 6. Eliminar nodo
    removed_node = editor_service.remove_node(wf.workflow_id, task_node.node_id)
    assert removed_node is True

    # 7. Intentar eliminar nodo START debe fallar por regla de protección
    with pytest.raises(ValueError, match="No se puede eliminar el nodo START"):
        editor_service.remove_node(wf.workflow_id, "start")


def test_editor_service_step_and_backtrack(editor_service):
    """Valida la ejecución paso a paso y el backtrack determinista desde el editor."""
    wf_id = "wf_demo_autonomous"

    # Avanzar un paso (START)
    step1 = editor_service.step_workflow(wf_id)
    assert step1["executed_node_id"] == "start"
    assert step1["status"] in ("RUNNING", "IDLE")

    # Ejecutar hasta el freno de aprobación de deploy_prod
    exec_res = editor_service.execute_workflow(wf_id, max_steps=10)
    assert exec_res["node_states"]["start"] == "COMPLETED"
    assert exec_res["node_states"]["research"] == "COMPLETED"
    assert exec_res["node_states"]["dev"] == "COMPLETED"

    # Aplicar backtrack determinista hacia el nodo "research"
    bt_res = editor_service.backtrack_workflow(wf_id, target_node_id="research")
    assert bt_res["success"] is True
    assert bt_res["context"]["node_states"]["research"] == "READY"
    # Sus descendientes vuelven a PENDING
    assert bt_res["context"]["node_states"]["decision"] == "PENDING"

    # Reiniciar workflow a IDLE
    editor_service.reset_execution(wf_id)
    fresh_engine = editor_service.get_or_create_engine(wf_id)
    assert fresh_engine.context.status.value == "IDLE"


def test_fastapi_workflow_endpoints(api_client):
    """Valida los endpoints HTTP de la API REST para el editor visual."""
    # 1. Listar workflows
    res = api_client.get("/v1/workflows")
    assert res.status_code == 200
    data = res.json()["data"]
    assert len(data) >= 1
    assert any(wf["workflow_id"] == "wf_demo_autonomous" for wf in data)

    # 2. Obtener detalle de workflow
    res_det = api_client.get("/v1/workflows/wf_demo_autonomous")
    assert res_det.status_code == 200
    wf_detail = res_det.json()["data"]
    assert "start" in wf_detail["nodes"]
    assert "end" in wf_detail["nodes"]

    # 3. Validar DAG
    res_val = api_client.post("/v1/workflows/wf_demo_autonomous/validate")
    assert res_val.status_code == 200
    val_data = res_val.json()["data"]
    assert val_data["is_valid"] is True
    assert len(val_data["topological_order"]) == 6

    # 4. Actualizar posición de un nodo tras arrastrar en UI
    res_pos = api_client.put(
        "/v1/workflows/wf_demo_autonomous/nodes/start/position",
        json={"x": 120.0, "y": 260.0},
    )
    assert res_pos.status_code == 200
    pos_data = res_pos.json()["data"]
    assert pos_data["position"]["x"] == 120.0
    assert pos_data["position"]["y"] == 260.0

    # 5. Actualizar propiedades del nodo (nombre, agente, inputs)
    res_up_node = api_client.put(
        "/v1/workflows/wf_demo_autonomous/nodes/research",
        json={
            "name": "Updated Deep Research",
            "agent_id": "custom_agent_x",
            "inputs": {"depth": "thorough", "max_tokens": 4096},
        },
    )
    assert res_up_node.status_code == 200
    up_node_data = res_up_node.json()["data"]
    assert up_node_data["name"] == "Updated Deep Research"
    assert up_node_data["agent_id"] == "custom_agent_x"
    assert up_node_data["inputs"]["depth"] == "thorough"

    # 6. Actualizar condición lógica de una arista
    res_up_edge = api_client.put(
        "/v1/workflows/wf_demo_autonomous/edges/e1",
        json={
            "label": "Condición Si Valida",
            "condition": {
                "field": "output.status",
                "operator": "==",
                "expected_value": "SUCCESS",
            },
        },
    )
    assert res_up_edge.status_code == 200
    up_edge_data = res_up_edge.json()["data"]
    assert up_edge_data["label"] == "Condición Si Valida"
    assert up_edge_data["condition"]["field"] == "output.status"
    assert up_edge_data["condition"]["operator"] == "=="
    assert up_edge_data["condition"]["expected_value"] == "SUCCESS"

    # 7. Servir la interfaz web interactiva drag & drop
    res_ui = api_client.get("/v1/workflows/editor/ui")
    assert res_ui.status_code == 200
    assert "PRAXEON - Visual Workflow Orchestrator" in res_ui.text
    assert "id=\"canvas\"" in res_ui.text
    assert "id=\"btn-run\"" in res_ui.text
