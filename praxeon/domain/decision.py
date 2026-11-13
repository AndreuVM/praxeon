"""Decisiones formales de política y recibos inmutables de auditoría (Cuadro 1)."""

from datetime import datetime, timezone
from enum import Enum
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, List, Optional, Union
import uuid
from pydantic import BaseModel, ConfigDict, Field

from praxeon.domain.assessment import CommandRiskAssessment, ProviderAssessment, RiskAssessment

logger = logging.getLogger(__name__)

# Versión del formato firmado del capability. 2 = JSON canónico (posterior a v1.2.0); 1 = campos unidos con ":" (solo al verificar).
RECEIPT_SIGNATURE_VERSION = 2


def utc_now() -> datetime:
    """Retorna el timestamp actual en UTC con timezone-awareness (Python 3.11+)."""
    return datetime.now(timezone.utc)



class ExecutionMode(str, Enum):
    """Modos canónicos de ejecución física de PRAXEON 1.0 (Sección 7.2)."""
    CONTAINER = "container"
    LOCAL_RESTRICTED = "local_restricted"
    FULL_ACCESS = "full_access"


class DecisionStatus(str, Enum):
    """Estados canónicos de decisión formal del supervisor."""
    ALLOW = "allow"      # Acción autorizada por la política actual
    BLOCK = "block"      # Denegación formal o imperativo de seguridad
    REPLAN = "replan"    # Trayectoria inadecuada; replanificación obligatoria
    ABSTAIN = "abstain"  # Abstención preventiva por incertidumbre o indisponibilidad


class PolicyDecision(BaseModel):
    """Decisión final consolidada por la PolicyEngine."""
    model_config = ConfigDict(frozen=True)

    status: DecisionStatus
    reason_codes: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    target_checkpoint: Optional[str] = None
    forbidden_tools: List[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    provider: Optional[ProviderAssessment] = None
    grounding: Optional[float] = None
    risk: Optional[RiskAssessment] = None
    operation_assessment: Optional[CommandRiskAssessment] = None


class DecisionReceipt(BaseModel):
    """Recibo criptográficamente auditable de una decisión de supervisión (Contrato Cuadro 1)."""
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    # 1. Contexto
    decision_id: str
    session_id: str
    action_id: str
    state_hash: str
    action_hash: str
    execution_mode: str = ExecutionMode.LOCAL_RESTRICTED.value
    actor: Optional[str] = None
    nonce: str = Field(default_factory=lambda: uuid.uuid4().hex)
    signature: Optional[str] = None
    expires_at: Optional[datetime] = None

    # 2. Proveedor
    provider_available: bool = True
    model_identifier: Optional[str] = None
    latency_ms: float = 0.0

    # 3. Razonamiento
    progress_score: Optional[float] = None
    grounded_score: Optional[float] = None
    loop_type: Optional[str] = None
    novelty_score: Optional[float] = None

    # 4. Riesgo
    risk_level: Optional[str] = None
    risk_reasons: List[str] = Field(default_factory=list)
    destructive_potential: bool = False
    operation_category: Optional[str] = None
    operation_assessment: Optional[Dict[str, Any]] = None

    # 5. Política
    decision_status: DecisionStatus = DecisionStatus.ALLOW
    reason_codes: List[str] = Field(default_factory=list)

    # 6. Ejecución
    is_executed: bool = False
    observation_id: Optional[str] = None
    execution_timestamp: Optional[datetime] = None
    timestamp: datetime = Field(default_factory=utc_now)

    def __init__(self, **data: Any):
        # Compatibilidad: si se pasa 'status' o 'decision' en lugar de 'decision_status'
        if "status" in data and "decision_status" not in data:
            data["decision_status"] = data.pop("status")
        elif "decision" in data and "decision_status" not in data:
            data["decision_status"] = data.pop("decision")
        super().__init__(**data)

    @property
    def decision(self) -> DecisionStatus:
        """Alias para compatibilidad hacia atrás con v0.2 temprana."""
        return self.decision_status

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Determina si el capability receipt ha superado su ventana temporal de validez."""
        if self.expires_at is None:
            return False
        current_time = now or utc_now()
        exp = self.expires_at
        if exp.tzinfo is None and current_time.tzinfo is not None:
            exp = exp.replace(tzinfo=timezone.utc)
        elif exp.tzinfo is not None and current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        return current_time > exp

    def to_capability_payload(self, allowed_tools: Optional[List[str]] = None) -> Optional["CapabilityPayload"]:
        """Convierte el recibo en un CapabilityPayload formal si el estatus es ALLOW; devuelve None si fue denegado/bloqueado."""
        if self.decision_status != DecisionStatus.ALLOW:
            return None
        return CapabilityPayload(
            capability_id=f"cap_{self.decision_id}",
            decision_id=self.decision_id,
            session_id=self.session_id,
            action_id=self.action_id,
            action_hash=self.action_hash,
            state_hash=self.state_hash,
            execution_mode=self.execution_mode,
            nonce=self.nonce,
            decision_status=self.decision_status,
            allowed_tools=allowed_tools or [],
            expires_at=self.expires_at,
            signature=self.signature,
            issued_at=self.timestamp,
            actor=self.actor,
        )


class CapabilityPayload(BaseModel):
    """Token formal de autorización emitido por PolicyEngine para autorizar la ejecución física."""
    model_config = ConfigDict(frozen=True)

    capability_id: str = Field(..., description="ID único del capability o correspondencia con decision_id")
    decision_id: str
    session_id: str
    action_id: str
    action_hash: str
    state_hash: str
    execution_mode: str = ExecutionMode.LOCAL_RESTRICTED.value
    nonce: str
    decision_status: DecisionStatus
    allowed_tools: List[str] = Field(default_factory=list)
    expires_at: Optional[datetime] = None
    signature: Optional[str] = None
    issued_at: datetime = Field(default_factory=utc_now)
    actor: Optional[str] = None

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Determina si el capability ha superado su ventana temporal de validez."""
        if self.expires_at is None:
            return False
        current_time = now or utc_now()
        exp = self.expires_at
        if exp.tzinfo is None and current_time.tzinfo is not None:
            exp = exp.replace(tzinfo=timezone.utc)
        elif exp.tzinfo is not None and current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        return current_time > exp



def compute_receipt_signature(
    secret_key: str,
    decision_id: str,
    session_id: str,
    action_hash: str,
    state_hash: str,
    nonce: str,
    decision_status: DecisionStatus,
    expires_at: Optional[datetime] = None,
    execution_mode: str = ExecutionMode.LOCAL_RESTRICTED.value,
) -> str:
    """HMAC-SHA256 del capability sobre JSON canónico (auditoría S-11).

    Antes se firmaba la concatenación de los campos con `:`; como algunos (p. ej. `session_id`) los elige el cliente y pueden
    contener `:`, el mismo texto firmado admitía varias lecturas. El JSON con claves ordenadas y un campo de versión no.
    """
    payload = json.dumps(
        {
            "v": RECEIPT_SIGNATURE_VERSION,
            "decision_id": decision_id,
            "session_id": session_id,
            "action_hash": action_hash,
            "state_hash": state_hash,
            "nonce": nonce,
            "status": _enum_value(decision_status),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "execution_mode": _enum_value(execution_mode),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _legacy_receipt_signature(
    secret_key: str,
    decision_id: str,
    session_id: str,
    action_hash: str,
    state_hash: str,
    nonce: str,
    decision_status: Any,
    expires_at: Optional[datetime] = None,
    execution_mode: Any = ExecutionMode.LOCAL_RESTRICTED.value,
) -> str:
    """Formato de firma anterior (campos unidos con `:`). Solo se acepta al verificar, durante una versión menor."""
    exp_str = expires_at.isoformat() if expires_at else "none"
    payload = (
        f"{decision_id}:{session_id}:{action_hash}:{state_hash}:{nonce}:"
        f"{_enum_value(decision_status)}:{exp_str}:{_enum_value(execution_mode)}"
    )
    return hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _enum_value(value: Any) -> str:
    return value.value if isinstance(value, Enum) else str(value)


def _signature_matches(secret_key: str, signature: str, **fields: Any) -> bool:
    """Compara en tiempo constante con la firma canónica y, durante la ventana de compatibilidad, con la antigua."""
    if hmac.compare_digest(signature, compute_receipt_signature(secret_key, **fields)):
        return True
    if hmac.compare_digest(signature, _legacy_receipt_signature(secret_key, **fields)):
        logger.warning(
            "Capability %s firmado con el formato anterior (campos unidos con ':'): se acepta por compatibilidad, "
            "dejará de aceptarse en la próxima versión menor.", fields.get("decision_id"),
        )
        return True
    return False


def sign_receipt(receipt: DecisionReceipt, secret_key: str) -> DecisionReceipt:
    """Firma un DecisionReceipt con la clave secreta y devuelve una instancia actualizada con su firma HMAC."""
    sig = compute_receipt_signature(
        secret_key=secret_key,
        decision_id=receipt.decision_id,
        session_id=receipt.session_id,
        action_hash=receipt.action_hash,
        state_hash=receipt.state_hash,
        nonce=receipt.nonce,
        decision_status=receipt.decision_status,
        expires_at=receipt.expires_at,
        execution_mode=receipt.execution_mode,
    )
    return receipt.model_copy(update={"signature": sig})


def verify_receipt_signature(secret_key: str, receipt: DecisionReceipt) -> bool:
    """Verifica de forma inmune a ataques de temporización si el recibo fue firmado con la clave del runtime."""
    if not receipt.signature:
        return False
    return _signature_matches(
        secret_key,
        receipt.signature,
        decision_id=receipt.decision_id,
        session_id=receipt.session_id,
        action_hash=receipt.action_hash,
        state_hash=receipt.state_hash,
        nonce=receipt.nonce,
        decision_status=receipt.decision_status,
        expires_at=receipt.expires_at,
        execution_mode=receipt.execution_mode,
    )


def verify_capability_signature(secret_key: str, capability: Union[CapabilityPayload, Dict[str, Any]]) -> bool:
    """Verifica de forma inmune a ataques de temporización la autenticidad del capability token emitido."""
    if isinstance(capability, dict):
        try:
            capability = CapabilityPayload(**capability)
        except Exception:
            return False
    if not capability.signature:
        return False
    return _signature_matches(
        secret_key,
        capability.signature,
        decision_id=capability.decision_id,
        session_id=capability.session_id,
        action_hash=capability.action_hash,
        state_hash=capability.state_hash,
        nonce=capability.nonce,
        decision_status=capability.decision_status,
        expires_at=capability.expires_at,
        execution_mode=capability.execution_mode,
    )


def compute_state_hash(state_dict: Any) -> str:
    """Calcula un hash SHA-256 determinista para el estado canónico."""
    if hasattr(state_dict, "to_snapshot"):
        state_dict = state_dict.to_snapshot()
    elif hasattr(state_dict, "model_dump"):
        state_dict = state_dict.model_dump()
    elif not isinstance(state_dict, dict):
        try:
            state_dict = dict(state_dict)
        except Exception:
            state_dict = {"repr": str(state_dict)}
    canonical = json.dumps(state_dict, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

