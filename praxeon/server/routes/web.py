"""Acceso a internet de los agentes: activarlo, dominios permitidos fuera de Full Access y clave de Brave Search.

La clave se guarda pero la API nunca la devuelve. Toda la ruta exige permisos de administración.
"""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from praxeon.server.dependencies import RuntimeApplicationService, get_runtime_service
from praxeon.server.schemas.common import APIResponse
from praxeon.webtools import WebError, WebTools

router = APIRouter(prefix="/v1/web", tags=["Web"])


class WebSettingsRequest(BaseModel):
    enabled: bool | None = None
    allowed_domains: list[str] | None = Field(default=None, max_length=200)
    search_provider: Literal["auto", "brave", "duckduckgo"] | None = Field(default=None, description="auto: Brave si hay clave, si no DuckDuckGo")
    brave_api_key: str | None = Field(default=None, max_length=300, description="Vacío borra la guardada; ausente la conserva")


def _web(service: RuntimeApplicationService = Depends(get_runtime_service)) -> WebTools:
    return service.web


@router.get("/settings", response_model=APIResponse[dict])  # type: ignore[type-arg]  # `dict` a secas: con `dict[str, Any]` cambiaría el nombre del esquema en el OpenAPI
def get_settings(web: WebTools = Depends(_web)) -> APIResponse[dict[str, Any]]:
    return APIResponse(data=web.settings.public())


@router.put("/settings", response_model=APIResponse[dict])  # type: ignore[type-arg]  # `dict` a secas: con `dict[str, Any]` cambiaría el nombre del esquema en el OpenAPI
def put_settings(req: WebSettingsRequest, web: WebTools = Depends(_web)) -> APIResponse[dict[str, Any]]:
    return APIResponse(data=web.settings.update(req.enabled, req.allowed_domains, req.brave_api_key, req.search_provider))


@router.post("/test", response_model=APIResponse[dict])  # type: ignore[type-arg]  # `dict` a secas: con `dict[str, Any]` cambiaría el nombre del esquema en el OpenAPI
def test_search(web: WebTools = Depends(_web)) -> APIResponse[dict[str, Any]]:
    """Hace una búsqueda de prueba para comprobar la clave y la conexión."""
    try:
        results, used, note = web.run_search("praxeon", 1)
    except WebError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return APIResponse(data={"ok": True, "results": len(results), "provider": used, "note": note.strip()})
