"""Esquemas para el catálogo y telemetría de providers (praxeon/server/schemas/provider.py)."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DecisionProviderInfo(BaseModel):
    """Información y estado operativo de un DecisionProvider registrado."""
    provider_id: str
    name: str
    model_id: str
    version: Optional[str] = None
    backend: str = "local"
    supported_backends: List[str] = Field(default_factory=list)
    description: str = ""
    available: bool = True
    installed: bool = True
    required_extra: Optional[str] = None
    average_latency_ms: Optional[float] = None
    average_latency_display: str = "N/A"
    fallbacks: List[str] = Field(default_factory=list)
    category: str = "decision_provider"


class LLMProviderInfo(BaseModel):
    """Información de un proveedor de LLM compatible."""
    provider_id: str
    name: str
    default_model: str
    type: str = "cloud"


class ProvidersCatalogResponse(BaseModel):
    """Respuesta completa del catálogo dinámico de providers."""
    decision_providers: List[DecisionProviderInfo]
    llm_providers: List[LLMProviderInfo]
