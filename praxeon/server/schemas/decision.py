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
    execution_mode: str = "local_restricted"
    capability: Optional[Dict[str, Any]] = None
    expires_at: Optional[datetime] = None
    operation_assessment: Optional[Dict[str, Any]] = None


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
    reason: Optional[str] = Field(None, description="Justificación u observaciones del revisor humano (obligatorio al rechazar)")
    actor: Optional[str] = Field("human_operator", description="Identidad del supervisor humano")
    operator_id: Optional[str] = Field("operator_admin", description="ID de usuario del operador humano")
    role: Optional[str] = Field("operator", description="Rol de autorización: 'viewer', 'operator', 'admin'")


class RejectDecisionRequest(BaseModel):
    """Petición explícita para rechazar una decisión en espera (REVIEW) con justificación obligatoria."""
    reason: str = Field(..., description="Justificación obligatoria del rechazo del operador")
    actor: Optional[str] = Field("human_operator", description="Identidad del supervisor humano")
    operator_id: Optional[str] = Field("operator_admin", description="ID de usuario del operador humano")
    role: Optional[str] = Field("operator", description="Rol de autorización: 'viewer', 'operator', 'admin'")


class ConfirmDecisionResponse(BaseModel):
    """Resultado de la confirmación humana."""
    model_config = ConfigDict(frozen=True)

    decision_id: str
    status: str  # ALLOW o BLOCKED
    message: str
    execution_mode: Optional[str] = "local_restricted"
    operator_id: Optional[str] = None
    role: Optional[str] = None
    capability: Optional[Dict[str, Any]] = None
    confirmed_at: datetime = Field(default_factory=datetime.utcnow)


class ExecuteDecisionRequest(BaseModel):
    """Petición para invocar la ejecución física con capability token."""
    capability_token: Optional[Dict[str, Any]] = Field(None, description="Capability token firmado emitido por el runtime")
    operator_id: Optional[str] = Field(None, description="Identificador del operador que dispara la ejecución")
    role: Optional[str] = Field("operator", description="Rol de autorización ('viewer', 'operator', 'admin')")


class ExecuteDecisionResponse(BaseModel):
    """Resultado de la ejecución física de la herramienta en sandbox o host."""
    model_config = ConfigDict(frozen=True)

    decision_id: str
    action_id: str
    output: str
    success: bool
    exit_code: int = 0
    execution_time_ms: float = 0.0
    execution_mode: str = "local_restricted"
    tier: str = "local_process"  # "container", "local_process", "dry_run", "full_access"
    fallback_occurred: bool = False
    is_error: bool = False
