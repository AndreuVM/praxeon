"""Rutas REST para catálogo y telemetría de providers (praxeon/server/routes/providers.py)."""

from typing import Any

from fastapi import APIRouter, Depends

from praxeon import agy_tools, vision

from praxeon.server.dependencies import RuntimeApplicationService, get_runtime_service
from praxeon.server.schemas.common import APIResponse
from praxeon.server.schemas.provider import ProvidersCatalogResponse

router = APIRouter(prefix="/v1/providers", tags=["Providers"])


@router.get("", response_model=APIResponse[ProvidersCatalogResponse])
def get_providers(
    service: RuntimeApplicationService = Depends(get_runtime_service),
) -> APIResponse[ProvidersCatalogResponse]:
    """Retorna el catálogo dinámico de Decision Providers y LLM Providers con disponibilidad y telemetría observada."""
    data = service.get_available_providers()
    return APIResponse(data=ProvidersCatalogResponse(**data))


@router.get("/agy/tools", response_model=APIResponse[dict[str, Any]])
def get_agy_tools() -> APIResponse[dict[str, Any]]:
    """Herramientas propias de agy que se pueden activar a mano, con su nivel de riesgo y el aviso que el usuario debe confirmar."""
    return APIResponse(data={"groups": agy_tools.catalog(), "images_available": vision.available()})
