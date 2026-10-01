"""Esquemas de petición y respuesta para sesiones de supervisión (praxeon/server/schemas/session.py)."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class CreateSessionRequest(BaseModel):
    """Petición para crear una nueva sesión de supervisión."""
    goal: str = Field(..., description="Objetivo o tarea que el agente debe resolver")
    session_id: Optional[str] = Field(None, description="Identificador único opcional; se genera si no se suministra")
    agent_name: Optional[str] = Field("CodingAgent", description="Nombre del agente autónomo")
    execution_mode: Optional[str] = Field("local_restricted", description="Modo de ejecución ('container', 'local_restricted', 'full_access')")
    confirmation_required_for_full_access: bool = Field(True, description="Exige confirmación explícita para activar full_access")
    allow_unattended_execution: Optional[bool] = Field(False, description="Permite omitir confirmación humana interactiva en full_access (Modo Autónomo)")
    autonomous: Optional[bool] = Field(False, description="Activa ejecución autónoma desatendida")
    workspace_root: Optional[str] = Field(None, description="Ruta raíz del espacio de trabajo")
    network_policy: Optional[str] = Field("isolated", description="Política de red ('isolated', 'restricted', 'host')")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadatos contextuales adicionales")


class SessionSummaryResponse(BaseModel):
    """Resumen de una sesión para listas y cabeceras."""
    model_config = ConfigDict(frozen=True)

    session_id: str
    goal: str
    status: str = "Active"  # "Active", "Completed", "Paused"
    agent_name: str = "CodingAgent"
    execution_mode: str = "local_restricted"
    workspace_root: Optional[str] = None
    operator_approval_status: Optional[str] = "Normal"
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
    execution_mode: str = "local_restricted"
    created_at: datetime
    tree: Dict[str, Any]
    summary: SessionSummaryResponse


class RunMissionRequest(BaseModel):
    """Petición para iniciar interactivamente una misión supervisada en tiempo real."""
    goal: str = Field(..., description="Prompt u objetivo inicial de la misión")
    session_id: Optional[str] = Field(None, description="Identificador de sesión opcional")
    agent_name: Optional[str] = Field("CodingAgent", description="Nombre del agente autónomo")
    execution_mode: Optional[str] = Field("local_restricted", description="Modo de ejecución física ('container', 'local_restricted', 'full_access')")
    workspace_root: Optional[str] = Field(None, description="Ruta raíz del workspace para la misión")
    network_policy: Optional[str] = Field("isolated", description="Política de red ('isolated', 'restricted', 'host')")
    llm_provider: Optional[str] = Field("simulator", description="Proveedor del LLM ('simulator', 'groq', 'ollama', 'gemini', 'openai', 'openrouter')")
    llm_model: Optional[str] = Field(None, description="Modelo específico de LLM")
    api_key: Optional[str] = Field(None, description="Clave de API opcional para el proveedor LLM")
    base_url: Optional[str] = Field(None, description="URL base opcional para endpoints locales o personalizados")
    supervisor: Optional[str] = Field("laya", description="Motor de supervisión ('laya', 'typesafe', 'cascade')")
    max_steps: Optional[int] = Field(25, ge=1, le=500, description="Límite máximo de pasos para la misión (por defecto 25, ampliable hasta 500)")
    step_delay_ms: Optional[int] = Field(900, description="Retardo en ms entre pasos para visualización en tiempo real")
    allow_unattended_execution: Optional[bool] = Field(False, description="Permite omitir confirmación humana interactiva en full_access (Modo Autónomo)")
    autonomous: Optional[bool] = Field(False, description="Activa ejecución autónoma desatendida")
    llm_failure_policy: Optional[str] = Field("synthetic_fallback", description="Política ante fallos del LLM: 'fail_closed' o 'synthetic_fallback'")
    chat_history: Optional[List[Dict[str, str]]] = Field(None, description="Historial previo de conversación para memoria multi-turno")

