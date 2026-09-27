"""Entidades de evaluación semántica de proveedores y análisis de riesgo operacional."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ProviderAssessment(BaseModel):
    """Juicio semántico multidimensional emitido por un ReasoningProvider (TypeSafe AI, LAYA o Replay)."""
    model_config = ConfigDict(frozen=True)

    provider: str
    model: Optional[str] = None
    available: bool = True
    confidence: float = 0.0
    loop_probability: Optional[float] = None
    grounded_probability: Optional[float] = None
    progress_probability: Optional[float] = None
    novelty_probability: Optional[float] = None
    analytical_jev: Optional[float] = None
    failure_reason: Optional[str] = None
    reason_codes: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RiskLevel(str, Enum):
    """Nivel de severidad operacional de una acción."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskAssessment(BaseModel):
    """Evaluación objetiva del riesgo operacional de una herramienta."""
    model_config = ConfigDict(frozen=True)

    level: RiskLevel
    requires_confirmation: bool = False
    executable: bool = True
    destructive_potential: bool = False
    reasons: List[str] = Field(default_factory=list)


class CommandCategory(str, Enum):
    """Taxonomía canónica de operaciones en PRAXEON 1.0 (Sección 4.2)."""
    INSPECTION = "inspection"
    BUILD_TEST = "build_test"
    PACKAGE_MANAGEMENT = "package_management"
    LOCAL_MUTATION = "local_mutation"
    NETWORK = "network"
    PROCESS_CONTROL = "process_control"
    PRIVILEGE = "privilege"
    DESTRUCTIVE = "destructive"
    REMOTE_MUTATION = "remote_mutation"
    UNKNOWN = "unknown"


class CommandRiskAssessment(BaseModel):
    """Clasificación contextual y multidimensional de una operación concreta (Sección 4.1)."""
    model_config = ConfigDict(frozen=True)

    operation: str
    category: CommandCategory
    risk_level: RiskLevel
    read_only: bool = False
    reversible: bool = True
    destructive: bool = False
    external_side_effect: bool = False
    network_access: bool = False
    privilege_escalation: bool = False
    confidence: float = 1.0
    reasons: List[str] = Field(default_factory=list)
    matched_rules: List[str] = Field(default_factory=list)
    classifier: str = "deterministic"  # deterministic / laya / typesafe
    context_hash: str = ""

    @property
    def is_read_only(self) -> bool:
        """Alias para read_only."""
        return self.read_only

    @property
    def is_reversible(self) -> bool:
        """Alias para reversible."""
        return self.reversible

