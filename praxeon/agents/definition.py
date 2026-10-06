"""Especificación formal de AgentDefinition y entidades de aprovisionamiento (F4-01).

Define el esquema canónico e inmutable para agentes dinámicos en PRAXEON:
- AgentStatus: Ciclo de vida operativo del agente.
- ModelConfig: Especificación del modelo, proveedor y parámetros de inferencia.
- RiskProfile: Políticas de contención, nivel de riesgo y confirmación humana.
- AgentContextPolicy: Cota de tokens de contexto, caché y condensación episódica.
- AgentDefinition: Definición formal completa con fingerprint SHA-256 inmutable.
"""

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, computed_field

from praxeon.domain.models import RiskLevel


class AgentStatus(str, Enum):
    """Estados del ciclo de vida de un agente dentro del pool del runtime."""
    ACTIVE = "ACTIVE"              # Listo para recibir y ejecutar tareas
    INACTIVE = "INACTIVE"          # Deshabilitado temporalmente
    PROVISIONING = "PROVISIONING"  # En proceso de arranque y enlace de dependencias
    TERMINATED = "TERMINATED"      # Desmantelado permanentemente
    QUARANTINED = "QUARANTINED"    # En cuarentena por fallo de integridad o deserialización


class ModelConfig(BaseModel):
    """Configuración de proveedor de razonamiento e inferencia."""
    model_config = ConfigDict(frozen=True)

    provider: str = Field(default="gemini", description="Proveedor del modelo (gemini, openai, anthropic, ollama, mock)")
    model_name: str = Field(default="gemini-1.5-pro", description="Identificador del modelo en el proveedor")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=4096, ge=1)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    timeout_seconds: int = Field(default=60, ge=1)


class RiskProfile(BaseModel):
    """Perfil de riesgo y políticas de contención asociadas al rol del agente."""
    model_config = ConfigDict(frozen=True)

    max_risk_level: RiskLevel = Field(default=RiskLevel.MEDIUM, description="Nivel máximo de riesgo tolerado sin bloqueo")
    require_human_confirmation: bool = Field(default=False, description="Si requiere aprobación humana en operaciones HIGH")
    auto_rollback_on_critical: bool = Field(default=True, description="Si dispara reversión automática al detectar anomalía crítica")


class AgentContextPolicy(BaseModel):
    """Políticas de gestión de contexto y memoria episódica."""
    model_config = ConfigDict(frozen=True)

    max_input_tokens: int = Field(default=4096, ge=256)
    enable_cache: bool = Field(default=True)
    auto_summarize: bool = Field(default=True)
    max_recent_observations: int = Field(default=10, ge=1)


class AgentDefinition(BaseModel):
    """Definición formal, canónica y versionada de un agente en PRAXEON."""
    model_config = ConfigDict(frozen=True)

    agent_id: str = Field(description="Identificador único del agente (ej. ag_dev_01)")
    name: str = Field(description="Nombre legible del agente")
    role: str = Field(description="Rol funcional del agente (Developer, Auditor, Researcher, etc.)")
    description: str = Field(default="")
    system_prompt: str = Field(description="Directiva raíz de identidad y comportamiento")
    version: int = Field(default=1, ge=1)
    status: AgentStatus = Field(default=AgentStatus.ACTIVE)
    model: ModelConfig = Field(default_factory=ModelConfig)
    allowed_tools: List[str] = Field(default_factory=lambda: ["*"], description="Lista blanca de herramientas ('*' para todas)")
    forbidden_tools: List[str] = Field(default_factory=list, description="Lista negra explícita de herramientas prohibidas")
    capabilities: List[str] = Field(default_factory=list, description="Capacidades operativas autorizadas")
    skills: List[str] = Field(default_factory=list, description="Catálogo de habilidades especializadas")
    risk_profile: RiskProfile = Field(default_factory=RiskProfile)
    context_policy: AgentContextPolicy = Field(default_factory=AgentContextPolicy)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def definition_hash(self) -> str:
        """Calcula una huella criptográfica determinista SHA-256 de la definición del agente."""
        canonical_payload = {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "system_prompt": self.system_prompt.strip(),
            "version": self.version,
            "model": self.model.model_dump(),
            "allowed_tools": sorted(self.allowed_tools),
            "forbidden_tools": sorted(self.forbidden_tools),
            "capabilities": sorted(self.capabilities),
            "skills": sorted(self.skills),
            "risk_profile": self.risk_profile.model_dump(),
            "context_policy": self.context_policy.model_dump(),
        }
        encoded = json.dumps(canonical_payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Evalúa si una herramienta está permitida para este agente según sus políticas."""
        t_clean = tool_name.strip()
        # 1. Lista negra explícita prevalece siempre
        if t_clean in self.forbidden_tools or "*" in self.forbidden_tools:
            return False

        # 2. Lista blanca
        if "*" in self.allowed_tools:
            return True
        return t_clean in self.allowed_tools

    def clone(
        self,
        new_agent_id: str,
        new_name: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> "AgentDefinition":
        """Genera una copia derivada con un nuevo identificador y ajustes opcionales."""
        data = self.model_dump()
        data["agent_id"] = new_agent_id
        data["name"] = new_name or f"{self.name} (Copy)"
        data["version"] = 1
        data["created_at"] = datetime.now(timezone.utc)
        data["updated_at"] = datetime.now(timezone.utc)

        if overrides:
            for k, v in overrides.items():
                data[k] = v

        return AgentDefinition(**data)

    def to_dict(self) -> Dict[str, Any]:
        """Serialización estructurada completa para persistencia y API."""
        dump = self.model_dump(mode="json")
        dump["definition_hash"] = self.definition_hash
        return dump

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentDefinition":
        """Instancia una definición a partir de un diccionario serializado."""
        clean_data = dict(data)
        clean_data.pop("definition_hash", None)
        if isinstance(clean_data.get("created_at"), str):
            clean_data["created_at"] = datetime.fromisoformat(clean_data["created_at"])
        if isinstance(clean_data.get("updated_at"), str):
            clean_data["updated_at"] = datetime.fromisoformat(clean_data["updated_at"])
        return cls(**clean_data)
