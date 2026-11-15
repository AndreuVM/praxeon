"""Esquemas de petición y respuesta para sesiones de supervisión (praxeon/server/schemas/session.py)."""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class CreateSessionRequest(BaseModel):
    """Petición para crear una nueva sesión de supervisión."""
    goal: str = Field(..., description="Objetivo o tarea que el agente debe resolver")
    session_id: str | None = Field(None, description="Identificador único opcional; se genera si no se suministra")
    agent_name: str | None = Field("CodingAgent", description="Nombre del agente autónomo")
    execution_mode: str | None = Field(None, description="Modo de ejecución ('auto' o vacío: aislado si el sistema lo permite; 'container', 'local_restricted', 'full_access')")
    confirmation_required_for_full_access: bool = Field(True, description="Exige confirmación explícita para activar full_access")
    full_access_confirmed: bool = Field(False, description="El operador confirmó explícitamente (doble confirmación) la activación de Full Access")
    require_step_confirmation: bool = Field(False, description="Opt-in: exigir confirmación humana por paso incluso en Full Access autorizado")
    allow_unattended_execution: bool | None = Field(False, description="Permite omitir confirmación humana interactiva en full_access (Modo Autónomo)")
    autonomous: bool | None = Field(False, description="Activa ejecución autónoma desatendida")
    workspace_root: str | None = Field(None, description="Ruta raíz del espacio de trabajo")
    network_policy: str | None = Field("isolated", description="Política de red ('isolated', 'restricted', 'host')")
    decision_model: Any | None = Field(None, description="Configuración tipada del modelo System-1 (DecisionModelConfig)")
    llm_config: dict[str, Any] | None = Field(None, description="Configuración explícita del LLM que razona")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadatos contextuales adicionales")


class SessionSummaryResponse(BaseModel):
    """Resumen de una sesión para listas y cabeceras."""
    model_config = ConfigDict(frozen=True)

    session_id: str
    goal: str
    status: str = "Active"  # "Active", "Completed", "Paused"
    agent_name: str = "CodingAgent"
    execution_mode: str = "local_restricted"
    isolation_backend: str | None = None      # appcontainer / bubblewrap / container, en modo aislado
    isolation_warning: str | None = None      # por qué la sesión no está aislada (o no puede ejecutar)
    workspace_root: str | None = None
    operator_approval_status: str | None = "Normal"
    created_at: datetime
    updated_at: datetime
    total_decisions: int = 0
    allowed_count: int = 0
    blocked_count: int = 0
    review_count: int = 0
    waiting_count: int = 0
    event_count: int = 0
    node_count: int = 0
    decision_model_effective: dict[str, Any] | None = None


class SessionSnapshotResponse(BaseModel):
    """Snapshot completo con el árbol de decisiones derivado."""
    model_config = ConfigDict(frozen=True)

    session_id: str
    goal: str
    status: str
    agent_name: str
    execution_mode: str = "local_restricted"
    created_at: datetime
    tree: dict[str, Any]
    summary: SessionSummaryResponse


class RunMissionRequest(BaseModel):
    """Petición para iniciar interactivamente una misión supervisada en tiempo real."""
    goal: str = Field(..., description="Prompt u objetivo inicial de la misión")
    workflow_id: str | None = Field(None, description="Workflow ACTIVO que gobierna la tarea (los agentes del grafo ejecutan sus nodos)")
    session_id: str | None = Field(None, description="Identificador de sesión opcional")
    agent_name: str | None = Field("CodingAgent", description="Nombre del agente autónomo")
    execution_mode: str | None = Field(None, description="Modo de ejecución física ('auto' o vacío: aislado si el sistema lo permite; 'container', 'local_restricted', 'full_access')")
    workspace_root: str | None = Field(None, description="Ruta raíz del workspace para la misión")
    network_policy: str | None = Field("isolated", description="Política de red ('isolated', 'restricted', 'host')")
    llm_provider: str | None = Field("simulator", description="Proveedor del LLM ('simulator', 'groq', 'ollama', 'gemini', 'openai', 'anthropic', 'claude_cli', 'agy', 'openrouter')")
    llm_model: str | None = Field(None, description="Modelo específico de LLM")
    api_key: str | None = Field(None, description="Clave de API opcional para el proveedor LLM")
    base_url: str | None = Field(None, description="URL base opcional para endpoints locales o personalizados")
    supervisor: str | None = Field("laya", description="Motor de supervisión ('laya', 'typesafe', 'cascade')")
    decision_model: Any | None = Field(None, description="Configuración tipada del modelo System-1 (DecisionModelConfig)")
    max_steps: int | None = Field(25, ge=0, le=100000, description="Límite máximo de pasos para la misión (por defecto 25). 0 = sin límite: la misión solo termina al concluir, por un fallo irrecuperable o cuando el operador la detiene")
    step_delay_ms: int | None = Field(900, description="Retardo en ms entre pasos para visualización en tiempo real")
    full_access_confirmed: bool = Field(False, description="El operador confirmó explícitamente (doble confirmación) la activación de Full Access")
    require_step_confirmation: bool = Field(False, description="Opt-in: exigir confirmación humana por paso incluso en Full Access autorizado")
    allow_unattended_execution: bool | None = Field(False, description="Permite omitir confirmación humana interactiva en full_access (Modo Autónomo)")
    autonomous: bool | None = Field(False, description="Activa ejecución autónoma desatendida")
    llm_failure_policy: str | None = Field("synthetic_fallback", description="Política ante fallos del LLM: 'fail_closed' o 'synthetic_fallback'")
    chat_history: list[dict[str, str]] | None = Field(None, description="Historial previo de conversación para memoria multi-turno")
    agy_tools: list[str] | None = Field(None, max_length=20, description="Grupos de herramientas propias de agy que el usuario activa (solo con el proveedor agy; ver GET /v1/providers/agy/tools). Las ejecuta agy por su cuenta, sin la política de PRAXEON")
    agy_tools_ack: list[str] | None = Field(None, max_length=20, description="Grupos de agy_tools que el usuario ha confirmado explícitamente; cada grupo activado debe estar aquí o la petición se rechaza")
    attachments: list[str] | None = Field(None, max_length=500, description="Rutas (relativas al workspace) de los archivos adjuntos al mensaje, subidos antes con /v1/workspace/attachments. Se validan en el servidor")

