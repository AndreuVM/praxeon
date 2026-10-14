"""Servidores MCP que PRAXEON consume: alta, revisión de sus herramientas, aprobación y ajuste de riesgo.

Dar de alta un servidor equivale a permitir que se ejecute un programa en esta máquina, por eso toda la ruta exige
permisos de administración.
"""

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from praxeon.integrations.mcp_client import McpServerConfig, ToolOverride
from praxeon.server.dependencies import get_runtime_service
from praxeon.server.schemas.common import APIResponse

router = APIRouter(prefix="/v1/mcp", tags=["MCP"])


class ServerRequest(BaseModel):
    command: Optional[str] = Field(default=None, description="Servidor local (stdio): ejecutable (npx, uvx, python…)")
    args: List[str] = Field(default_factory=list)
    url: Optional[str] = Field(
        default=None,
        description="Servidor remoto (SSE o HTTP streamable). Puede llevar «${VARIABLE}» para el token. Al editar, vacío conserva la guardada "
        "(la API nunca devuelve la URL completa).",
    )
    headers: Dict[str, Optional[str]] = Field(default_factory=dict, description="Cabeceras HTTP (p. ej. Authorization); null conserva el valor guardado")
    transport: Literal["auto", "sse", "streamable_http"] = "auto"
    env: Dict[str, Optional[str]] = Field(
        default_factory=dict,
        description="Variables del servidor; usa ${VAR} para tomarlas del entorno de PRAXEON. Un valor null conserva el ya guardado "
        "(la API nunca devuelve los valores, así que al editar se reenvían las claves sin tocar).",
    )
    cwd: Optional[str] = None
    trust_hints: bool = False
    timeout_s: float = Field(default=60.0, gt=0, le=3600)


class ApproveRequest(BaseModel):
    tools_hash: str = Field(..., min_length=8, description="Huella de las herramientas que has revisado")


def _mcp(service=Depends(get_runtime_service)):
    return service.mcp


def _missing(name: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Servidor MCP '{name}' no encontrado.")


@router.get("/servers", response_model=APIResponse[List[Dict[str, Any]]])
def list_servers(mcp=Depends(_mcp)):
    return APIResponse(data=mcp.status())


@router.put("/servers/{name}", response_model=APIResponse[Dict[str, Any]])
def put_server(name: str, req: ServerRequest, mcp=Depends(_mcp)):
    """Crea o actualiza un servidor. Si cambia lo que se ejecuta, queda desactivado y sin aprobar."""
    current = mcp.store.get(name)
    fields = req.model_dump()
    stored_env = current.env if current else {}
    kept_missing = [k for k, v in fields["env"].items() if v is None and k not in stored_env]
    if kept_missing:
        raise HTTPException(status_code=422, detail=f"No hay valor guardado para: {', '.join(kept_missing)}")
    fields["env"] = {k: (stored_env[k] if v is None else v) for k, v in fields["env"].items()}
    stored_headers = current.headers if current else {}
    missing_headers = [k for k, v in fields["headers"].items() if v is None and k not in stored_headers]
    if missing_headers:
        raise HTTPException(status_code=422, detail=f"No hay valor guardado para las cabeceras: {', '.join(missing_headers)}")
    fields["headers"] = {k: (stored_headers[k] if v is None else v) for k, v in fields["headers"].items()}
    if not (fields.get("url") or "").strip() and not (fields.get("command") or "").strip() and current is not None and current.url:
        fields["url"] = current.url   # editar sin reescribir la URL (que la API no devuelve): se conserva
    try:
        cfg = McpServerConfig(
            name=name, **fields,
            enabled=current.enabled if current else False,
            approved_hash=current.approved_hash if current else None,
            tools=current.tools if current else {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    saved = mcp.store.save(cfg)
    if current is not None and not saved.enabled:
        mcp.stop_server(name)
    return APIResponse(data=mcp.describe(name))


@router.delete("/servers/{name}", response_model=APIResponse[Dict[str, Any]])
def delete_server(name: str, mcp=Depends(_mcp)):
    if not mcp.remove(name):
        raise _missing(name)
    return APIResponse(data={"deleted": name})


@router.post("/servers/{name}/enable", response_model=APIResponse[Dict[str, Any]])
def enable_server(name: str, mcp=Depends(_mcp)):
    """Activa y arranca el servidor para que puedas revisar sus herramientas (aún no se exponen al agente)."""
    try:
        return APIResponse(data=mcp.enable(name))
    except KeyError as exc:
        raise _missing(name) from exc


@router.post("/servers/{name}/disable", response_model=APIResponse[Dict[str, Any]])
def disable_server(name: str, mcp=Depends(_mcp)):
    try:
        return APIResponse(data=mcp.disable(name))
    except KeyError as exc:
        raise _missing(name) from exc


@router.post("/servers/{name}/restart", response_model=APIResponse[Dict[str, Any]])
def restart_server(name: str, mcp=Depends(_mcp)):
    try:
        return APIResponse(data=mcp.start_server(name))
    except KeyError as exc:
        raise _missing(name) from exc


@router.post("/servers/{name}/approve", response_model=APIResponse[Dict[str, Any]])
def approve_server(name: str, req: ApproveRequest, mcp=Depends(_mcp)):
    """Aprueba las herramientas revisadas (por huella) y las expone a los agentes."""
    try:
        return APIResponse(data=mcp.approve(name, req.tools_hash))
    except KeyError as exc:
        raise _missing(name) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.put("/servers/{name}/tools/{tool}", response_model=APIResponse[Dict[str, Any]])
def put_tool_override(name: str, tool: str, req: ToolOverride, mcp=Depends(_mcp)):
    """Ajusta riesgo, solo-lectura, confirmación o desactiva una herramienta concreta."""
    cfg = mcp.store.get(name)
    if cfg is None:
        raise _missing(name)
    mcp.store.update(name, tools={**cfg.tools, tool: req})
    mcp.reconfigure(name)
    return APIResponse(data=mcp.describe(name))
