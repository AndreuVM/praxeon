"""Esquemas de respuesta y confirmación de decisiones para PRAXEON 1.0 (Sección 3.3 y 6.3)."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ProviderEvaluationDTO(BaseModel):
    """Evaluación emitida por un proveedor semántico (LAYA / TypeSafe)."""
    model_config = ConfigDict(frozen=True)

    name: str
    score: float
    verdict: str  # ALLOW, REVIEW, BLOCK


class RiskDTO(BaseModel):
    """Evaluación de riesgo operacional."""
    model_config = ConfigDict(frozen=True)

    level: str  # LOW, MEDIUM, HIGH, CRITICAL
    score: float
    reasons: List[str] = Field(default_factory=list)


class PolicyDTO(BaseModel):
    """Directiva y reglas activadas por PolicyEngine."""
    model_config = ConfigDict(frozen=True)

    decision: str  # ALLOW, BLOCK, REQUIRE_HUMAN_CONFIRMATION, REPLAN
    reason_codes: List[str] = Field(default_factory=list)
    requires_confirmation: bool = False


class DecisionResponse(BaseModel):
    """Contrato de respuesta inmediata para una propuesta (Sección 3.3)."""
    model_config = ConfigDict(frozen=True)

    decision_id: str
    session_id: str
    status: str  # ALLOW, REVIEW, BLOCK, PENDING
    action_hash: str
    risk: RiskDTO
    providers: List[ProviderEvaluationDTO] = Field(default_factory=list)
    policy: PolicyDTO
    capability: Optional[Dict[str, Any]] = None
    expires_at: Optional[datetime] = None


class DecisionDetailResponse(BaseModel):
    """Detalle completo contextualizado en 4 pestañas para el Decision Inspector (Sección 6.3)."""
    model_config = ConfigDict(frozen=True)

    decision_id: str
    session_id: str
    action_id: str

    # Pestaña 1: Decision
    decision_tab: Dict[str, Any]

    # Pestaña 2: Evidence
    evidence_tab: Dict[str, Any]

    # Pestaña 3: Policy
    policy_tab: Dict[str, Any]

    # Pestaña 4: Receipt
    receipt_tab: Dict[str, Any]


class ConfirmDecisionRequest(BaseModel):
    """Petición humana para aprobar o rechazar una decisión en espera (REVIEW)."""
    approved: bool = Field(..., description="True para autorizar la acción; False para bloquearla")
    reason: Optional[str] = Field(None, description="Justificación u observaciones del revisor humano")
    actor: Optional[str] = Field("human_operator", description="Identidad del supervisor humano")


class ConfirmDecisionResponse(BaseModel):
    """Resultado de la confirmación humana."""
    model_config = ConfigDict(frozen=True)

    decision_id: str
    status: str  # ALLOW o BLOCKED
    message: str
    capability: Optional[Dict[str, Any]] = None
    confirmed_at: datetime = Field(default_factory=datetime.utcnow)


class ExecuteDecisionRequest(BaseModel):
    """Petición para invocar la ejecución física en sandbox con capability token."""
    capability_token: Optional[Dict[str, Any]] = Field(None, description="Capability token firmado emitido por el runtime")


class ExecuteDecisionResponse(BaseModel):
    """Resultado de la ejecución física de la herramienta en sandbox."""
    model_config = ConfigDict(frozen=True)

    decision_id: str
    action_id: str
    output: str
    success: bool
    exit_code: int = 0
    execution_time_ms: float = 0.0
    tier: str = "local_process"  # "container", "local_process", "dry_run"
    fallback_occurred: bool = False
    is_error: bool = False
