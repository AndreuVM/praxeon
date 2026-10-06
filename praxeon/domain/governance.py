"""Gobernanza explícita del Fallback Sintético y Procedencia de Acciones (praxeon/domain/governance.py).

Especificación PRAXEON P1:
Permite alternar formalmente entre:
- FAIL_CLOSED: Bloqueo estricto ante ausencia o caída del LLM; prohibido el fallback heurístico/simulado no solicitado.
- BEST_EFFORT: Degradación elegante a simulación o respuestas sintéticas marcadas explícitamente en auditoría.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import os
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


class FallbackMode(str, Enum):
    """Modos de gobernanza operativa ante fallos de conectividad o cuota del LLM."""
    FAIL_CLOSED = "FAIL_CLOSED"
    BEST_EFFORT = "BEST_EFFORT"


def resolve_fallback_mode(mode: Optional[Any] = None) -> FallbackMode:
    """Resuelve el modo de gobernanza desde el argumento, variable de entorno o configuración predeterminada."""
    if mode is not None:
        if isinstance(mode, FallbackMode):
            return mode
        val_str = str(mode).strip().upper().replace("-", "_")
        if val_str in ("FAIL_CLOSED", "STRICT", "CLOSED"):
            return FallbackMode.FAIL_CLOSED
        if val_str in ("BEST_EFFORT", "ALLOW_SIMULATION", "FALLBACK", "EFFORT"):
            return FallbackMode.BEST_EFFORT

    env_val = os.getenv("PRAXEON_FALLBACK_MODE", "").strip().upper().replace("-", "_")
    if env_val in ("FAIL_CLOSED", "STRICT", "CLOSED"):
        return FallbackMode.FAIL_CLOSED

    return FallbackMode.BEST_EFFORT


class ActionProvenance(BaseModel):
    """Metadatos de procedencia y trazabilidad de una acción propuesta o ejecutada."""
    model_config = ConfigDict(frozen=True)

    synthetic_fallback: bool = Field(
        default=False,
        description="Indica si la acción se generó mediante fallback heurístico/sintético",
    )
    model_source: str = Field(
        default="llm:unknown",
        description="Identificador del emisor (ej. 'llm:gemini:gemini-1.5-flash', 'synthetic:fallback')",
    )
    governance_mode: FallbackMode = Field(
        default=FallbackMode.BEST_EFFORT,
        description="Modo de gobernanza en vigor al emitir la acción",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp UTC de generación",
    )
