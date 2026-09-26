"""Esquemas base y respuestas comunes para la API REST de PRAXEON 1.0."""

from datetime import datetime
from typing import Any, Dict, Generic, List, Optional, TypeVar
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """Envoltorio estándar de respuesta exitosa."""
    model_config = ConfigDict(frozen=True)

    success: bool = True
    data: T
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class APIErrorResponse(BaseModel):
    """Respuesta estructurada de error."""
    model_config = ConfigDict(frozen=True)

    success: bool = False
    error_code: str
    message: str
    details: Optional[Dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class PaginationMeta(BaseModel):
    total: int
    offset: int = 0
    limit: int = 100
    has_more: bool = False
