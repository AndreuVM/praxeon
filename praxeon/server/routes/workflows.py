"""Rutas REST para el Editor Visual y Motor de Orquestación de Workflows (praxeon/server/routes/workflows.py) (F6-04)."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from praxeon.server.dependencies import get_current_tenant
from praxeon.server.schemas.common import APIResponse
from praxeon.workflows.editor_service import WorkflowEditorService
from praxeon.workflows.models import (
    EdgeCondition,
    NodeType,
    UIPosition,
)

router = APIRouter(prefix="/v1/workflows", tags=["Workflows"])

# Instancia de fallback del servicio de editor
_editor_service = WorkflowEditorService()


def get_workflow_editor_service() -> WorkflowEditorService:
    try:
        from praxeon.server.dependencies import get_runtime_service
        svc = get_runtime_service()
        if svc and hasattr(svc, "get_workflow_editor_service"):
            return svc.get_workflow_editor_service()
    except Exception:
        pass
    return _editor_service


class CreateWorkflowRequest(BaseModel):
    name: str = Field(description="Nombre del nuevo flujo")
    description: str = Field(default="", description="Descripción del flujo")
    workflow_id: Optional[str] = None


class AddNodeRequest(BaseModel):
    name: str
    node_type: NodeType
    x: float = 300.0
    y: float = 250.0
    agent_id: Optional[str] = None
    tool_name: Optional[str] = None
    inputs: Dict[str, Any] = Field(default_factory=dict)
    control_config: Optional[Dict[str, Any]] = None


class UpdateNodePositionRequest(BaseModel):
    x: float
    y: float


class UpdateNodeRequest(BaseModel):
    name: Optional[str] = None
    node_type: Optional[NodeType] = None
    agent_id: Optional[str] = None
    tool_name: Optional[str] = None
    inputs: Optional[Dict[str, Any]] = None
    control_config: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class ApproveNodeRequest(BaseModel):
    approved: bool = True
    comment: str = ""



class ConnectNodesRequest(BaseModel):
    from_node: str
    to_node: str
    condition: Optional[EdgeCondition] = None
    label: str = ""


class UpdateEdgeRequest(BaseModel):
    label: Optional[str] = None
    condition: Optional[EdgeCondition] = None


class StartExecutionRequest(BaseModel):
    variables: Dict[str, Any] = Field(default_factory=dict)


class BacktrackRequest(BaseModel):
    target_node_id: str


@router.get("", response_model=APIResponse[List[Dict[str, Any]]])
def list_workflows():
    """Lista todos los flujos de trabajo registrados."""
    service = get_workflow_editor_service()
    return APIResponse(data=service.list_workflows())


@router.post("", response_model=APIResponse[Dict[str, Any]])
def create_workflow(payload: CreateWorkflowRequest):
    """Crea un nuevo flujo de trabajo."""
    service = get_workflow_editor_service()
    wf = service.create_workflow(
        name=payload.name,
        description=payload.description,
        workflow_id=payload.workflow_id,
    )
    return APIResponse(data=wf.to_dict())


@router.get("/{workflow_id}", response_model=APIResponse[Dict[str, Any]])
def get_workflow_detail(workflow_id: str):
    """Obtiene la definición canónica del workflow."""
    service = get_workflow_editor_service()
    wf = service.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' no encontrado.",
        )
    return APIResponse(data=wf.to_dict())


@router.post("/{workflow_id}/nodes", response_model=APIResponse[Dict[str, Any]])
def add_node(workflow_id: str, payload: AddNodeRequest):
    """Añade un nodo al flujo de trabajo."""
    service = get_workflow_editor_service()
    try:
        node = service.add_node(
            workflow_id=workflow_id,
            name=payload.name,
            node_type=payload.node_type,
            position=UIPosition(x=payload.x, y=payload.y),
            agent_id=payload.agent_id,
            tool_name=payload.tool_name,
            inputs=payload.inputs,
            control_config=payload.control_config,
        )
        return APIResponse(data=node.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/{workflow_id}/nodes/{node_id}/position", response_model=APIResponse[Dict[str, Any]])
def update_node_position(workflow_id: str, node_id: str, payload: UpdateNodePositionRequest):
    """Actualiza las coordenadas de un nodo en el lienzo tras arrastrar."""
    service = get_workflow_editor_service()
    try:
        updated = service.update_node_position(
            workflow_id=workflow_id,
            node_id=node_id,
            x=payload.x,
            y=payload.y,
        )
        return APIResponse(data=updated.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/{workflow_id}/nodes/{node_id}", response_model=APIResponse[Dict[str, Any]])
def update_node(workflow_id: str, node_id: str, payload: UpdateNodeRequest):
    """Actualiza las propiedades de un nodo (nombre, agente asignado, tipo, herramienta, inputs, control_config)."""
    service = get_workflow_editor_service()
    try:
        updated = service.update_node(
            workflow_id=workflow_id,
            node_id=node_id,
            name=payload.name,
            node_type=payload.node_type,
            agent_id=payload.agent_id,
            tool_name=payload.tool_name,
            inputs=payload.inputs,
            control_config=payload.control_config,
            metadata=payload.metadata,
        )
        return APIResponse(data=updated.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc



@router.delete("/{workflow_id}/nodes/{node_id}", response_model=APIResponse[Dict[str, Any]])
def delete_node(workflow_id: str, node_id: str):
    """Elimina un nodo y sus aristas conectadas."""
    service = get_workflow_editor_service()
    try:
        ok = service.remove_node(workflow_id, node_id)
        if not ok:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nodo no encontrado.")
        return APIResponse(data={"deleted": True, "node_id": node_id})
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{workflow_id}/edges", response_model=APIResponse[Dict[str, Any]])
def connect_nodes(workflow_id: str, payload: ConnectNodesRequest):
    """Crea una arista dirigida entre dos nodos."""
    service = get_workflow_editor_service()
    try:
        edge = service.connect_nodes(
            workflow_id=workflow_id,
            from_node=payload.from_node,
            to_node=payload.to_node,
            condition=payload.condition,
            label=payload.label,
        )
        return APIResponse(data=edge.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/{workflow_id}/edges/{edge_id}", response_model=APIResponse[Dict[str, Any]])
def update_edge(workflow_id: str, edge_id: str, payload: UpdateEdgeRequest):
    """Actualiza una arista existente (etiqueta, condición lógica de transición/unión)."""
    service = get_workflow_editor_service()
    try:
        edge = service.update_edge(
            workflow_id=workflow_id,
            edge_id=edge_id,
            label=payload.label,
            condition=payload.condition,
        )
        return APIResponse(data=edge.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/{workflow_id}/edges/{edge_id}", response_model=APIResponse[Dict[str, Any]])
def delete_edge(workflow_id: str, edge_id: str):
    """Elimina una conexión existente."""
    service = get_workflow_editor_service()
    ok = service.disconnect_nodes(workflow_id, edge_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arista no encontrada.")
    return APIResponse(data={"deleted": True, "edge_id": edge_id})


@router.post("/{workflow_id}/validate", response_model=APIResponse[Dict[str, Any]])
def validate_workflow(workflow_id: str):
    """Verifica si el grafo es un DAG válido y no contiene ciclos ni nodos huérfanos."""
    service = get_workflow_editor_service()
    wf = service.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow no encontrado.")
    errors = wf.validate_graph()
    order = wf.topological_sort() if not errors else []
    return APIResponse(data={"is_valid": len(errors) == 0, "errors": errors, "warnings": wf.lint_graph(), "topological_order": order})


@router.post("/{workflow_id}/execute", response_model=APIResponse[Dict[str, Any]])
def execute_workflow(workflow_id: str, max_steps: int = Query(100, ge=1, le=1000)):
    """Ejecuta el flujo de trabajo supervisado por gobernanza física."""
    service = get_workflow_editor_service()
    try:
        result = service.execute_workflow(workflow_id=workflow_id, max_steps=max_steps)
        return APIResponse(data=result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{workflow_id}/step", response_model=APIResponse[Dict[str, Any]])
def step_workflow(workflow_id: str):
    """Avanza un paso en el workflow."""
    service = get_workflow_editor_service()
    try:
        step_result = service.step_workflow(workflow_id)
        return APIResponse(data=step_result)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{workflow_id}/backtrack", response_model=APIResponse[Dict[str, Any]])
def backtrack_workflow(workflow_id: str, payload: BacktrackRequest):
    """Aplica retroceso determinista hacia un nodo objetivo."""
    service = get_workflow_editor_service()
    try:
        res = service.backtrack_workflow(workflow_id, payload.target_node_id)
        return APIResponse(data=res)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{workflow_id}/reset", response_model=APIResponse[Dict[str, Any]])
def reset_workflow(workflow_id: str):
    """Reinicia la ejecución del workflow a IDLE."""
    service = get_workflow_editor_service()
    service.reset_execution(workflow_id)
    return APIResponse(data={"reset": True, "workflow_id": workflow_id})


@router.post("/{workflow_id}/nodes/{node_id}/approve", response_model=APIResponse[Dict[str, Any]])
def approve_workflow_node(
    workflow_id: str,
    node_id: str,
    payload: ApproveNodeRequest,
    tenant=Depends(get_current_tenant),
):
    """Aprueba o rechaza la ejecución de un nodo en estado WAITING_APPROVAL."""
    service = get_workflow_editor_service()
    try:
        res = service.approve_node(
            workflow_id=workflow_id,
            node_id=node_id,
            approved=payload.approved,
            comment=payload.comment,
            approver_id=tenant.user_id,
        )
        return APIResponse(data=res)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _workflow_service():
    from praxeon.server.dependencies import get_runtime_service
    return get_runtime_service().get_workflow_service()


@router.post("/{workflow_id}/executions", response_model=APIResponse[Dict[str, Any]])
def start_execution(workflow_id: str, payload: Optional[StartExecutionRequest] = None):
    """Inicia una ejecución independiente (un motor por ejecución) gestionada por el scheduler."""
    execution = _workflow_service().run_workflow_execution(
        workflow_id, initial_variables=(payload.variables if payload else None)
    )
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Workflow '{workflow_id}' no encontrado.")
    return APIResponse(data=execution.to_dict())


@router.get("/executions/{execution_id}", response_model=APIResponse[Dict[str, Any]])
def get_execution(execution_id: str):
    """Estado actual (o final persistido) de una ejecución."""
    svc = _workflow_service()
    engine = svc.scheduler.get_active_engines().get(execution_id)
    execution = engine.get_execution() if engine else svc.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ejecución no encontrada.")
    return APIResponse(data=execution.to_dict())


@router.post("/executions/{execution_id}/cancel", response_model=APIResponse[Dict[str, Any]])
def cancel_execution(execution_id: str):
    """Cancela una ejecución activa."""
    execution = _workflow_service().cancel_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ejecución no encontrada.")
    return APIResponse(data=execution.to_dict())


@router.post("/executions/{execution_id}/nodes/{node_id}/approve", response_model=APIResponse[Dict[str, Any]])
def approve_execution_node(
    execution_id: str,
    node_id: str,
    payload: ApproveNodeRequest,
    tenant=Depends(get_current_tenant),
):
    """Aprueba o rechaza un nodo en espera de una ejecución activa."""
    ok = _workflow_service().approve_execution_node(
        execution_id, node_id, payload.approved, payload.comment, approver_id=tenant.user_id
    )
    if ok is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ejecución no activa.")
    return APIResponse(data={"success": ok, "execution_id": execution_id, "node_id": node_id})


@router.get("/templates/canonical", response_model=APIResponse[List[Dict[str, Any]]])
def list_canonical_templates():
    """Lista las plantillas canónicas de flujos de trabajo multiagente."""
    service = get_workflow_editor_service()
    return APIResponse(data=service.list_templates())


@router.post("/templates/{template_key}/instantiate", response_model=APIResponse[Dict[str, Any]])
def instantiate_canonical_template(template_key: str, name: Optional[str] = None):
    """Crea un nuevo workflow a partir de una plantilla canónica."""
    service = get_workflow_editor_service()
    try:
        wf = service.instantiate_template(template_key=template_key, name=name)
        return APIResponse(data=wf.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
