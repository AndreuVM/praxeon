"""Entidades y Modelos de Dominio para el Enrutamiento Inteligente de Agentes (F7-01).

Define las especificaciones de requerimientos de tareas, estrategias de despacho
y recibos formales de decisiones de enrutamiento:
- TaskRequirement: Especificación formal de la misión, directivas y capacidades requeridas.
- RoutingStrategyType: Modos canónicos de selección de agentes.
- RoutingDecision: Resultado formal, auditable y trazable de la asignación de un agente.
"""

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from praxeon.agents.protocol import MessagePriority
from praxeon.domain.assessment import RiskLevel


class TaskComplexity(str, Enum):
    """Nivel de complejidad inferido para una tarea."""
    TRIVIAL = "TRIVIAL"        # Consultas simples, lecturas directas
    LOW = "LOW"                # Modificaciones menores, análisis de archivos únicos
    MEDIUM = "MEDIUM"          # Refactorización, creación de nuevos módulos
    HIGH = "HIGH"              # Modificación de arquitectura central, seguridad
    CRITICAL = "CRITICAL"      # Migraciones masivas, cambios de infraestructura raíz


class RoutingStrategyType(str, Enum):
    """Estrategias canónicas de enrutamiento de agentes."""
    MANUAL = "MANUAL"          # Asignación forzada por el usuario o llamador
    STATIC = "STATIC"          # Mapeo estático por configuración de roles
    RULE_BASED = "RULE_BASED"  # Reglas deterministas de capacidades y herramientas
    SEMANTIC = "SEMANTIC"      # Similitud semántica con directivas de agentes
    COST_AWARE = "COST_AWARE"  # Optimización de coste preservando umbral de calidad
    ADAPTIVE = "ADAPTIVE"      # Ponderación combinada adaptativa con feedback


class TaskRequirement(BaseModel):
    """Requerimientos y restricciones declarativas de una tarea a ser enrutada."""
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(description="Identificador único de la tarea")
    prompt: str = Field(description="Descripción o directiva en lenguaje natural")
    required_capabilities: List[str] = Field(default_factory=list, description="Capacidades operacionales requeridas")
    required_tools: List[str] = Field(default_factory=list, description="Herramientas indispensables para la tarea")
    target_files: List[str] = Field(default_factory=list, description="Archivos involucrados")
    expected_output_type: Optional[str] = Field(default=None, description="Tipo de artefacto esperado")
    priority: MessagePriority = Field(default=MessagePriority.NORMAL)
    max_acceptable_cost: Optional[float] = Field(default=None, ge=0.0)
    complexity: TaskComplexity = Field(default=TaskComplexity.MEDIUM)
    inferred_risk: RiskLevel = Field(default=RiskLevel.LOW)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def fingerprint(self) -> str:
        """Huella determinista de la especificación de la tarea."""
        payload = {
            "task_id": self.task_id,
            "prompt": self.prompt.strip(),
            "capabilities": sorted(self.required_capabilities),
            "tools": sorted(self.required_tools),
            "files": sorted(self.target_files),
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]


class RoutingDecision(BaseModel):
    """Decisión formal, inmutable y auditable de enrutamiento hacia un agente."""
    model_config = ConfigDict(frozen=True)

    decision_id: str
    task_id: str
    selected_agent_id: str
    confidence: float = Field(ge=0.0, le=1.0, description="Nivel de confianza en la idoneidad del agente")
    strategy_used: RoutingStrategyType
    alternative_agent_ids: List[str] = Field(default_factory=list)
    rationale: str = Field(description="Justificación técnica de la selección")
    matched_capabilities: List[str] = Field(default_factory=list)
    estimated_cost: float = Field(default=0.0, ge=0.0)
    escalation_level: int = Field(default=0, ge=0, description="Nivel de escalado aplicado si hubo re-enrutamiento")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serializa la decisión a diccionario estándar."""
        dump = self.model_dump(mode="json")
        return dump
