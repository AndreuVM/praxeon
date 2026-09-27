"""Esquemas de propuesta de acciones para la API REST de PRAXEON 1.0 (Sección 3.2)."""

from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


class ProposeActionRequest(BaseModel):
    """Contrato formal de propuesta de acción por parte de un agente (Sección 3.2)."""
    model_config = ConfigDict(frozen=True)

    action_id: Optional[str] = Field(None, description="Identificador único de la acción propuesta")
    parent_id: Optional[str] = Field(None, description="Identificador del nodo padre en el árbol de decisión para bifurcaciones y retrocesos")
    tool: str = Field(..., description="Nombre canónico de la herramienta propuesta (ej: 'git', 'read_file')")
    operation: Optional[str] = Field(None, description="Operación específica o descripción corta (ej: 'push')")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Argumentos estructurados de la herramienta")
    thought_rationale: Optional[str] = Field(None, description="Razonamiento o justificación previa del agente")
    provenance: Dict[str, Any] = Field(
        default_factory=lambda: {"source": "ExternalAgent", "step": 1},
        description="Origen de la acción (agente emisor y paso de la trayectoria)",
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contexto relevante de la sesión (ej: meta actual, supuestos)",
    )
