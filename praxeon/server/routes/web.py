"""Acceso a internet de los agentes: activarlo, dominios permitidos fuera de Full Access y clave de Brave Search.

La clave se guarda pero la API nunca la devuelve. Toda la ruta exige permisos de administración.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from praxeon.server.dependencies import get_runtime_service
from praxeon.server.schemas.common import APIResponse
from praxeon.web import WebError

router = APIRouter(prefix="/v1/web", tags=["Web"])


class WebSettingsRequest(BaseModel):
    enabled: Optional[bool] = None
    allowed_domains: Optional[List[str]] = Field(default=None, max_length=200)
    brave_api_key: Optional[str] = Field(default=None, max_length=300, description="Vacío borra la guardada; ausente la conserva")


def _web(service=Depends(get_runtime_service)):
    return service.web


@router.get("/settings", response_model=APIResponse[dict])
def get_settings(web=Depends(_web)):
    return APIResponse(data=web.settings.public())


@router.put("/settings", response_model=APIResponse[dict])
def put_settings(req: WebSettingsRequest, web=Depends(_web)):
    return APIResponse(data=web.settings.update(req.enabled, req.allowed_domains, req.brave_api_key))


@router.post("/test", response_model=APIResponse[dict])
def test_search(web=Depends(_web)):
    """Hace una búsqueda de prueba para comprobar la clave y la conexión."""
    try:
        results = web._provider_factory(web.settings.api_key()).search("praxeon", 1)
    except WebError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return APIResponse(data={"ok": True, "results": len(results)})
