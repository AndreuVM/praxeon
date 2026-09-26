"""Esquemas de petición y respuesta para sesiones de supervisión (praxeon/server/schemas/session.py)."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class CreateSessionRequest(BaseModel):
    """Petición para crear una nueva sesión de supervisión."""
    goal: str = Field(..., description="Objetivo o tarea que el agente debe resolver")
    session_id: Optional[str] = Field(None, description="Identificador único opcional; se genera si no se suministra")
    agent_name: Optional[str] = Field("CodingAgent", description="Nombre del agente autónomo")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadatos contextuales adicionales")


class SessionSummaryResponse(BaseModel):
    """Resumen de una sesión para listas y cabeceras."""
    model_config = ConfigDict(frozen=True)

    session_id: str
    goal: str
    status: str = "Active"  # "Active", "Completed", "Paused"
    agent_name: str = "CodingAgent"
    created_at: datetime
    updated_at: datetime
    total_decisions: int = 0
    allowed_count: int = 0
    blocked_count: int = 0
    review_count: int = 0
    waiting_count: int = 0
    event_count: int = 0
    node_count: int = 0


class SessionSnapshotResponse(BaseModel):
    """Snapshot completo con el árbol de decisiones derivado."""
    model_config = ConfigDict(frozen=True)

    session_id: str
    goal: str
    status: str
    agent_name: str
    created_at: datetime
    tree: Dict[str, Any]
    summary: SessionSummaryResponse
