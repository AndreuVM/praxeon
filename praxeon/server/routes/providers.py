"""Rutas REST para catálogo y telemetría de providers (praxeon/server/routes/providers.py)."""

from typing import Any, Dict
from fastapi import APIRouter, Depends

from praxeon.server.dependencies import RuntimeApplicationService, get_runtime_service
from praxeon.server.schemas.common import APIResponse
from praxeon.server.schemas.provider import ProvidersCatalogResponse

router = APIRouter(prefix="/v1/providers", tags=["Providers"])


@router.get("", response_model=APIResponse[ProvidersCatalogResponse])
def get_providers(
    service: RuntimeApplicationService = Depends(get_runtime_service),
):
    """Retorna el catálogo dinámico de Decision Providers y LLM Providers con disponibilidad y telemetría observada."""
    data = service.get_available_providers()
    return APIResponse(data=ProvidersCatalogResponse(**data))
