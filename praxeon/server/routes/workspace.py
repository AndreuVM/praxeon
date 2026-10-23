"""Workspace del proyecto: consultar, elegir, explorar carpetas, gestionar recientes y acceder a sus archivos."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from praxeon import workspace_files, workspace_store
from praxeon.server.schemas.common import APIResponse

router = APIRouter(prefix="/v1/workspace", tags=["Workspace"])


class SelectWorkspaceRequest(BaseModel):
    path: str = Field(..., min_length=1, description="Carpeta que será el workspace por defecto")


class ForgetWorkspaceRequest(BaseModel):
    path: str = Field(..., min_length=1)


class SaveFileRequest(BaseModel):
    path: str = Field(..., min_length=1, description="Ruta relativa al workspace")
    content: str
    etag: Optional[str] = Field(None, description="etag leído; si ya no coincide se devuelve 409")
    encoding: str = Field("utf-8", description="Codificación con la que se leyó (se conserva al guardar)")


class CreateEntryRequest(BaseModel):
    path: str = Field(..., min_length=1, description="Ruta relativa del archivo o carpeta nuevos")
    kind: str = Field(..., pattern="^(file|dir)$")


class RenameEntryRequest(BaseModel):
    path: str = Field(..., min_length=1)
    new_path: str = Field(..., min_length=1)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, workspace_files.WorkspaceFileNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, workspace_files.WorkspaceTooLarge):
        return HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc))
    if isinstance(exc, (workspace_files.WorkspaceConflict, workspace_files.WorkspaceExists)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


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


# --- FE-001: árbol de archivos del workspace activo ---------------------------------------


@router.get("/tree", response_model=APIResponse[Dict[str, Any]])
def workspace_tree(path: str = "", show_hidden: bool = False):
    """Entradas de una carpeta del workspace (carga perezosa, carpetas primero)."""
    try:
        return APIResponse(data=workspace_files.list_tree(path, show_hidden=show_hidden))
    except workspace_files.WorkspacePathError as exc:
        raise _http_error(exc) from exc


# --- FE-003: visor y editor de archivos ---------------------------------------------------


@router.get("/file", response_model=APIResponse[Dict[str, Any]])
def read_workspace_file(path: str):
    """Contenido de texto y etag de un archivo; los binarios devuelven solo metadatos."""
    try:
        return APIResponse(data=workspace_files.read_file(path))
    except workspace_files.WorkspacePathError as exc:
        raise _http_error(exc) from exc


@router.get("/file/raw")
def raw_workspace_file(path: str):
    """Bytes del archivo con su Content-Type (PDF, imágenes) para previsualizar."""
    try:
        target, mime = workspace_files.read_raw(path)
    except workspace_files.WorkspacePathError as exc:
        raise _http_error(exc) from exc
    return FileResponse(target, media_type=mime, headers={"X-Content-Type-Options": "nosniff"})


@router.put("/file", response_model=APIResponse[Dict[str, Any]])
def save_workspace_file(req: SaveFileRequest):
    """Guarda el archivo de forma atómica; 409 si cambió en disco desde la lectura."""
    try:
        return APIResponse(data=workspace_files.write_file(req.path, req.content, req.etag, req.encoding))
    except workspace_files.WorkspaceConflict as exc:
        raise _http_error(exc) from exc
    except workspace_files.WorkspacePathError as exc:
        raise _http_error(exc) from exc


# --- gestión: crear, renombrar y eliminar archivos y carpetas -------------------------------------------------------


def _manage(action, *args):
    try:
        return APIResponse(data=action(*args))
    except (workspace_files.WorkspacePathError, workspace_files.WorkspaceExists, workspace_files.WorkspaceConflict) as exc:
        raise _http_error(exc) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"No se pudo completar la operación: {exc}") from exc


@router.post("/entry", response_model=APIResponse[Dict[str, Any]], status_code=status.HTTP_201_CREATED)
def create_workspace_entry(req: CreateEntryRequest):
    """Crea un archivo vacío o una carpeta (409 si ya existe)."""
    return _manage(workspace_files.create_entry, req.path, req.kind)


@router.patch("/entry", response_model=APIResponse[Dict[str, Any]])
def rename_workspace_entry(req: RenameEntryRequest):
    """Renombra o mueve dentro del workspace (409 si el destino existe)."""
    return _manage(workspace_files.rename_entry, req.path, req.new_path)


@router.delete("/entry", response_model=APIResponse[Dict[str, Any]])
def delete_workspace_entry(path: str):
    """Elimina un archivo o una carpeta con todo su contenido. No se puede deshacer."""
    return _manage(workspace_files.delete_entry, path)


@router.post("/upload", response_model=APIResponse[Dict[str, Any]], status_code=status.HTTP_201_CREATED)
async def upload_workspace_file(request: Request, path: str, overwrite: bool = False):
    """Sube un archivo (cuerpo = bytes en bruto) a `path`, creando las carpetas que falten. 409 si existe y no se sobrescribe."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > workspace_files.MAX_UPLOAD_BYTES:
        raise _http_error(workspace_files.WorkspaceTooLarge(f"El archivo supera el límite de {workspace_files.MAX_UPLOAD_BYTES // (1024 * 1024)} MB."))
    chunks, received = [], 0
    async for chunk in request.stream():           # se corta en cuanto pasa del límite, aunque no declare Content-Length
        received += len(chunk)
        if received > workspace_files.MAX_UPLOAD_BYTES:
            raise _http_error(workspace_files.WorkspaceTooLarge(f"El archivo supera el límite de {workspace_files.MAX_UPLOAD_BYTES // (1024 * 1024)} MB."))
        chunks.append(chunk)
    return _manage(workspace_files.save_upload, path, chunks, overwrite)
