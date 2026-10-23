"""Skills (SK-001): listar las SKILL.md descubiertas y habilitarlas o deshabilitarlas.

Habilitar una skill hace que sus instrucciones lleguen al prompt de los agentes, por eso la ruta exige permisos de administración.
"""

import os
import subprocess
import sys
from typing import Any, Dict, List, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status

from praxeon import workspace_store
from praxeon.server.dependencies import effective_client_host, get_runtime_service
from praxeon.server.websocket import is_remote_client
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


def open_in_file_manager(path: str) -> None:
    """Abre una carpeta en el explorador de archivos del sistema (Windows, macOS o Linux)."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606 - es una carpeta que acabamos de crear/validar, no entrada del usuario
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


@router.post("/open-folder", response_model=APIResponse[Dict[str, Any]])
def open_skills_folder(request: Request, scope: Literal["user", "project"] = "user", skills=Depends(_skills)):
    """Abre la carpeta de skills en el explorador del sistema, para dejar ahí las nuevas (se crea si no existe).

    Solo desde la propia máquina: abrir una ventana en el equipo del servidor no tiene sentido (ni es seguro) para un cliente remoto.
    """
    if is_remote_client(effective_client_host(request)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo se puede abrir la carpeta desde la propia máquina.")
    folder = skills.user_dir() if scope == "user" else skills.project_dir(_workspace())
    if folder is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No hay un workspace activo.")
    try:
        open_in_file_manager(str(folder))
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"No se pudo abrir la carpeta: {exc}") from exc
    return APIResponse(data={"path": str(folder), "scope": scope})


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
