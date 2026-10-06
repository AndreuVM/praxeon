"""Esquemas DTO para el API REST de Gestión de Agentes (praxeon/server/schemas/agent.py)."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from praxeon.agents.definition import (
    AgentContextPolicy,
    AgentStatus,
    ModelConfig,
    RiskProfile,
)


class CreateAgentRequest(BaseModel):
    """Solicitud de creación o registro de un agente."""
    agent_id: Optional[str] = Field(default=None, description="Identificador único del agente (se autogenera si no se especifica)")
    name: str = Field(description="Nombre legible del agente")
    role: str = Field(default="Assistant", description="Rol funcional del agente")
    description: str = Field(default="", description="Descripción del propósito del agente")
    system_prompt: str = Field(description="Directiva raíz de identidad y comportamiento")
    status: AgentStatus = Field(default=AgentStatus.ACTIVE, description="Estado operativo inicial")
    model: Optional[ModelConfig] = Field(default=None, description="Configuración del modelo de inferencia")
    allowed_tools: List[str] = Field(default_factory=lambda: ["*"], description="Herramientas autorizadas")
    forbidden_tools: List[str] = Field(default_factory=list, description="Herramientas prohibidas")
    capabilities: List[str] = Field(default_factory=list, description="Capacidades operativas autorizadas")
    skills: List[str] = Field(default_factory=list, description="Habilidades especializadas")
    risk_profile: Optional[RiskProfile] = Field(default=None, description="Perfil de riesgo y contención")
    context_policy: Optional[AgentContextPolicy] = Field(default=None, description="Políticas de contexto")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadatos adicionales")


class UpdateAgentRequest(BaseModel):
    """Solicitud de actualización parcial de un agente."""
    name: Optional[str] = Field(default=None, description="Nombre legible del agente")
    role: Optional[str] = Field(default=None, description="Rol funcional del agente")
    description: Optional[str] = Field(default=None, description="Descripción del propósito del agente")
    system_prompt: Optional[str] = Field(default=None, description="Directiva raíz de identidad y comportamiento")
    status: Optional[AgentStatus] = Field(default=None, description="Estado operativo del agente")
    model: Optional[ModelConfig] = Field(default=None, description="Configuración del modelo de inferencia")
    allowed_tools: Optional[List[str]] = Field(default=None, description="Herramientas autorizadas")
    forbidden_tools: Optional[List[str]] = Field(default=None, description="Herramientas prohibidas")
    capabilities: Optional[List[str]] = Field(default=None, description="Capacidades operativas autorizadas")
    skills: Optional[List[str]] = Field(default=None, description="Habilidades especializadas")
    risk_profile: Optional[RiskProfile] = Field(default=None, description="Perfil de riesgo y contención")
    context_policy: Optional[AgentContextPolicy] = Field(default=None, description="Políticas de contexto")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Metadatos adicionales")


class CreateAgentVersionRequest(BaseModel):
    """Solicitud de creación de una nueva versión inmutable para un agente."""
    name: Optional[str] = Field(default=None, description="Nuevo nombre para la versión")
    role: Optional[str] = Field(default=None, description="Nuevo rol funcional")
    description: Optional[str] = Field(default=None, description="Nueva descripción")
    system_prompt: Optional[str] = Field(default=None, description="Nueva directiva de comportamiento")
    model: Optional[ModelConfig] = Field(default=None, description="Nueva configuración del modelo")
    allowed_tools: Optional[List[str]] = Field(default=None, description="Herramientas autorizadas")
    forbidden_tools: Optional[List[str]] = Field(default=None, description="Herramientas prohibidas")
    capabilities: Optional[List[str]] = Field(default=None, description="Capacidades operativas autorizadas")
    skills: Optional[List[str]] = Field(default=None, description="Habilidades especializadas")
    risk_profile: Optional[RiskProfile] = Field(default=None, description="Perfil de riesgo y contención")
    context_policy: Optional[AgentContextPolicy] = Field(default=None, description="Políticas de contexto")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Metadatos adicionales")


class AgentDTO(BaseModel):
    """Representación pública estructurada de un agente."""
    agent_id: str
    name: str
    role: str
    description: str
    system_prompt: str
    version: int
    status: AgentStatus
    definition_hash: str
    model: ModelConfig
    allowed_tools: List[str]
    forbidden_tools: List[str]
    capabilities: List[str]
    skills: List[str]
    risk_profile: RiskProfile
    context_policy: AgentContextPolicy
    created_at: datetime
    updated_at: datetime
    metadata: Dict[str, Any]


class AgentVersionSummaryDTO(BaseModel):
    """Resumen de una versión inmutable registrada de un agente."""
    id: Optional[str] = None
    agent_id: str
    version: int
    definition_hash: str
    created_at: str
