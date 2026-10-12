"""Workspace del proyecto: consultar, elegir, explorar carpetas y gestionar recientes."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from praxeon import workspace_store
from praxeon.server.schemas.common import APIResponse

router = APIRouter(prefix="/v1/workspace", tags=["Workspace"])


class SelectWorkspaceRequest(BaseModel):
    path: str = Field(..., min_length=1, description="Carpeta que será el workspace por defecto")


class ForgetWorkspaceRequest(BaseModel):
    path: str = Field(..., min_length=1)


@router.get("", response_model=APIResponse[Dict[str, Any]])
def get_workspace():
    """Workspace en uso, de dónde sale (elegido / variable de entorno / autodetectado) y los recientes."""
    return APIResponse(data=workspace_store.describe_workspace())


@router.put("", response_model=APIResponse[Dict[str, Any]])
def select_workspace(req: SelectWorkspaceRequest):
    """Fija el workspace por defecto de las nuevas misiones."""
    try:
        workspace_store.select_workspace(req.path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return APIResponse(data=workspace_store.describe_workspace())


@router.delete("", response_model=APIResponse[Dict[str, Any]])
def clear_workspace():
    """Quita la elección y vuelve a la detección automática."""
    workspace_store.clear_selection()
    return APIResponse(data=workspace_store.describe_workspace())


@router.post("/forget", response_model=APIResponse[Dict[str, Any]])
def forget_workspace(req: ForgetWorkspaceRequest):
    """Quita una carpeta de la lista de recientes."""
    workspace_store.forget_recent(req.path)
    return APIResponse(data=workspace_store.describe_workspace())


@router.get("/validate", response_model=APIResponse[Dict[str, Any]])
def validate_workspace(path: str):
    """Comprueba si una ruta escrita a mano es una carpeta utilizable."""
    norm, reason = workspace_store.validate_directory(path)
    return APIResponse(
        data={
            "path": norm,
            "valid": reason is None,
            "reason": reason,
            "hints": workspace_store.project_hints(norm) if reason is None else [],
        }
    )


@router.get("/browse", response_model=APIResponse[Dict[str, Any]])
def browse_workspace(path: Optional[str] = None, show_hidden: bool = False):
    """Subcarpetas de una ruta, para elegir el workspace sin escribir rutas."""
    try:
        return APIResponse(data=workspace_store.browse_directory(path, show_hidden=show_hidden))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
