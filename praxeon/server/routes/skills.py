"""Skills (SK-001): listar las SKILL.md descubiertas y habilitarlas o deshabilitarlas.

Habilitar una skill hace que sus instrucciones lleguen al prompt de los agentes, por eso la ruta exige permisos de administración.
"""

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status

from praxeon import workspace_store
from praxeon.server.dependencies import get_runtime_service
from praxeon.server.schemas.common import APIResponse

router = APIRouter(prefix="/v1/skills", tags=["Skills"])


def _skills(service=Depends(get_runtime_service)):
    return service.skills


def _workspace() -> str:
    return workspace_store.describe_workspace().get("path") or ""


@router.get("", response_model=APIResponse[List[Dict[str, Any]]])
def list_skills(skills=Depends(_skills)):
    """Skills de la carpeta de usuario y del workspace activo, con su estado (enabled / disabled / changed / invalid)."""
    return APIResponse(data=skills.list(_workspace()))


@router.get("/{name}", response_model=APIResponse[Dict[str, Any]])
def get_skill(name: str, skills=Depends(_skills)):
    """Instrucciones completas de una skill, para revisarlas antes de habilitarla."""
    info = skills.get(name, _workspace())
    if info is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Skill '{name}' no encontrada.")
    return APIResponse(data={k: info[k] for k in ("name", "description", "body", "source", "path", "sha256", "size")})


@router.post("/{name}/enable", response_model=APIResponse[Dict[str, Any]])
def enable_skill(name: str, skills=Depends(_skills)):
    try:
        return APIResponse(data=skills.set_enabled(name, True, _workspace()))
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Skill '{name}' no encontrada o inválida.") from None


@router.post("/{name}/disable", response_model=APIResponse[Dict[str, Any]])
def disable_skill(name: str, skills=Depends(_skills)):
    return APIResponse(data=skills.set_enabled(name, False, _workspace()))
