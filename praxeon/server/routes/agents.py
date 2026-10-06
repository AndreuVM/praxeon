"""Rutas REST para la Gestión del Catálogo de Agentes (/v1/agents) (F4-04)."""

from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status

from praxeon.agents.definition import AgentDefinition, AgentStatus
from praxeon.server.dependencies import get_agent_service
from praxeon.server.schemas.agent import (
    AgentDTO,
    CreateAgentRequest,
    CreateAgentVersionRequest,
    UpdateAgentRequest,
)
from praxeon.server.schemas.common import APIResponse
from praxeon.server.services.agent_service import AgentService

router = APIRouter(prefix="/v1/agents", tags=["Agents"])


@router.get("", response_model=APIResponse[List[Dict[str, Any]]])
def list_agents(
    status_filter: Optional[AgentStatus] = Query(None, alias="status", description="Filtrar por estado"),
    role: Optional[str] = Query(None, description="Filtrar por rol funcional"),
    capability: Optional[str] = Query(None, description="Filtrar por capacidad"),
    skill: Optional[str] = Query(None, description="Filtrar por habilidad"),
    tag: Optional[str] = Query(None, description="Filtrar por tag, capacidad o habilidad"),
    service: AgentService = Depends(get_agent_service),
):
    """Lista todos los agentes registrados aplicando filtros opcionales."""
    agents = service.list_agents(status=status_filter, role=role)

    if capability:
        cap_clean = capability.lower().strip()
        agents = [a for a in agents if any(cap_clean == c.lower().strip() for c in a.capabilities)]

    if skill:
        skill_clean = skill.lower().strip()
        agents = [a for a in agents if any(skill_clean == s.lower().strip() for s in a.skills)]

    if tag:
        tag_clean = tag.lower().strip()

        def matches_tag(agent: AgentDefinition) -> bool:
            if any(tag_clean == c.lower().strip() for c in agent.capabilities):
                return True
            if any(tag_clean == s.lower().strip() for s in agent.skills):
                return True
            tags = agent.metadata.get("tags", [])
            if isinstance(tags, list) and any(tag_clean == str(t).lower().strip() for t in tags):
                return True
            return False

        agents = [a for a in agents if matches_tag(a)]

    return APIResponse(data=[a.to_dict() for a in agents])


@router.post("", response_model=APIResponse[Dict[str, Any]], status_code=status.HTTP_201_CREATED)
def create_agent(
    payload: CreateAgentRequest,
    service: AgentService = Depends(get_agent_service),
):
    """Registra un nuevo agente calculando su hash criptográfico determinista."""
    agent_id = payload.agent_id or f"ag_{uuid.uuid4().hex[:8]}"

    if service.get_agent(agent_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El agente con ID '{agent_id}' ya está registrado. Usa PATCH para modificarlo.",
        )

    data = payload.model_dump(exclude_unset=True)
    data["agent_id"] = agent_id

    # Limpiar campos nulos para que apliquen los defaults del modelo
    for optional_field in ("model", "risk_profile", "context_policy"):
        if data.get(optional_field) is None:
            data.pop(optional_field, None)

    try:
        agent = AgentDefinition(**data)
        registered = service.register_agent(agent)
        return APIResponse(data=registered.to_dict())
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Definición de agente inválida: {str(exc)}",
        )


@router.get("/{agent_id}", response_model=APIResponse[Dict[str, Any]])
def get_agent_detail(
    agent_id: str,
    service: AgentService = Depends(get_agent_service),
):
    """Obtiene la definición canónica y el estado de un agente."""
    agent = service.get_agent(agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agente con ID '{agent_id}' no encontrado.",
        )
    return APIResponse(data=agent.to_dict())


@router.patch("/{agent_id}", response_model=APIResponse[Dict[str, Any]])
def update_agent(
    agent_id: str,
    payload: UpdateAgentRequest,
    service: AgentService = Depends(get_agent_service),
):
    """Actualiza parcialmente un agente e incrementa su versión determinista."""
    existing = service.get_agent(agent_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agente con ID '{agent_id}' no encontrado.",
        )

    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not updates:
        return APIResponse(data=existing.to_dict())

    try:
        updated = service.update_agent(agent_id, updates)
        return APIResponse(data=updated.to_dict())
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error al actualizar agente '{agent_id}': {str(exc)}",
        )


@router.delete("/{agent_id}", response_model=APIResponse[Dict[str, Any]])
def delete_agent(
    agent_id: str,
    hard_delete: bool = Query(False, description="Si es True, elimina físicamente del almacén"),
    service: AgentService = Depends(get_agent_service),
):
    """Elimina lógicamente (TERMINATED) o físicamente un agente."""
    existing = service.get_agent(agent_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agente con ID '{agent_id}' no encontrado.",
        )

    success = service.delete_agent(agent_id, hard_delete=hard_delete)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"No se pudo eliminar el agente '{agent_id}'.",
        )

    return APIResponse(
        data={
            "agent_id": agent_id,
            "deleted": True,
            "hard_delete": hard_delete,
            "status": "REMOVED" if hard_delete else AgentStatus.TERMINATED.value,
        }
    )


@router.post("/{agent_id}/versions", response_model=APIResponse[Dict[str, Any]], status_code=status.HTTP_201_CREATED)
def create_agent_version(
    agent_id: str,
    payload: CreateAgentVersionRequest,
    service: AgentService = Depends(get_agent_service),
):
    """Crea una nueva versión inmutable para el agente con los cambios provistos."""
    existing = service.get_agent(agent_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agente con ID '{agent_id}' no encontrado.",
        )

    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    try:
        new_version = service.update_agent(agent_id, updates)
        return APIResponse(data=new_version.to_dict())
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error al versionar agente '{agent_id}': {str(exc)}",
        )


@router.get("/{agent_id}/versions", response_model=APIResponse[List[Dict[str, Any]]])
def list_agent_versions(
    agent_id: str,
    service: AgentService = Depends(get_agent_service),
):
    """Obtiene el historial inmutable de versiones de un agente."""
    existing = service.get_agent(agent_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agente con ID '{agent_id}' no encontrado.",
        )

    registry = service.get_registry()
    history = registry.get_agent_history(agent_id)
    return APIResponse(data=history)


@router.get("/{agent_id}/versions/{version}", response_model=APIResponse[Dict[str, Any]])
def get_agent_historical_version(
    agent_id: str,
    version: int,
    service: AgentService = Depends(get_agent_service),
):
    """Obtiene una versión histórica inmutable específica de un agente."""
    existing = service.get_agent(agent_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agente con ID '{agent_id}' no encontrado.",
        )

    registry = service.get_registry()
    historical = registry.get_agent_version(agent_id, version)
    if not historical:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Versión {version} no encontrada para el agente '{agent_id}'.",
        )
    return APIResponse(data=historical.to_dict())
