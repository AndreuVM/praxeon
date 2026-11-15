"""Esquemas de propuesta de acciones para la API REST de PRAXEON 1.0 (Sección 3.2)."""

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ProposeActionRequest(BaseModel):
    """Contrato formal de propuesta de acción por parte de un agente (Sección 3.2)."""
    model_config = ConfigDict(frozen=True)

    action_id: str | None = Field(default=None, description="Identificador único de la acción propuesta")
    parent_id: str | None = Field(default=None, description="Identificador del nodo padre en el árbol de decisión para bifurcaciones y retrocesos")
    tool: str = Field(..., description="Nombre canónico de la herramienta propuesta (ej: 'git', 'read_file')")
    operation: str | None = Field(default=None, description="Operación específica o descripción corta (ej: 'push')")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Argumentos estructurados de la herramienta")
    thought_rationale: str | None = Field(default=None, description="Razonamiento o justificación previa del agente")
    provenance: dict[str, Any] = Field(
        default_factory=lambda: {"source": "ExternalAgent", "step": 1},
        description="Origen de la acción (agente emisor y paso de la trayectoria)",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Contexto relevante de la sesión (ej: meta actual, supuestos)",
    )
    synthetic_fallback: bool = Field(
        default=False,
        description="Indica si la acción proviene de un fallback sintético",
    )
    model_source: str | None = Field(
        default=None,
        description="Identificador del modelo de inferencia de la acción (ej: 'llm:gemini', 'synthetic:fallback')",
    )
