"""Decisiones formales de política y recibos inmutables de auditoría (Cuadro 1)."""

from datetime import datetime, UTC
from enum import Enum
import hashlib
import hmac
import json
import logging
from typing import Any, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field

from praxeon.domain.assessment import CommandRiskAssessment, ProviderAssessment, RiskAssessment

logger = logging.getLogger(__name__)

# Versión del formato firmado del capability. 2 = JSON canónico (posterior a v1.2.0); 1 = campos unidos con ":" (solo al verificar).
RECEIPT_SIGNATURE_VERSION = 2


def utc_now() -> datetime:
    """Retorna el timestamp actual en UTC con timezone-awareness (Python 3.11+)."""
    return datetime.now(UTC)



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
    reason_codes: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    target_checkpoint: str | None = None
    forbidden_tools: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    provider: ProviderAssessment | None = None
    grounding: float | None = None
    risk: RiskAssessment | None = None
    operation_assessment: CommandRiskAssessment | None = None


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
    actor: str | None = None
    nonce: str = Field(default_factory=lambda: uuid.uuid4().hex)
    signature: str | None = None
    expires_at: datetime | None = None

    # 2. Proveedor
    provider_available: bool = True
    model_identifier: str | None = None
    latency_ms: float = 0.0

    # 3. Razonamiento
    progress_score: float | None = None
    grounded_score: float | None = None
    loop_type: str | None = None
    novelty_score: float | None = None

    # 4. Riesgo
    risk_level: str | None = None
    risk_reasons: list[str] = Field(default_factory=list)
    destructive_potential: bool = False
    operation_category: str | None = None
    operation_assessment: dict[str, Any] | None = None

    # 5. Política
    decision_status: DecisionStatus = DecisionStatus.ALLOW
    reason_codes: list[str] = Field(default_factory=list)

    # 6. Ejecución
    is_executed: bool = False
    observation_id: str | None = None
    execution_timestamp: datetime | None = None
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

    def is_expired(self, now: datetime | None = None) -> bool:
        """Determina si el capability receipt ha superado su ventana temporal de validez."""
        if self.expires_at is None:
            return False
        current_time = now or utc_now()
        exp = self.expires_at
        if exp.tzinfo is None and current_time.tzinfo is not None:
            exp = exp.replace(tzinfo=UTC)
        elif exp.tzinfo is not None and current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)
        return current_time > exp

    def to_capability_payload(self, allowed_tools: list[str] | None = None) -> Optional["CapabilityPayload"]:
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
    allowed_tools: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    signature: str | None = None
    issued_at: datetime = Field(default_factory=utc_now)
    actor: str | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        """Determina si el capability ha superado su ventana temporal de validez."""
        if self.expires_at is None:
            return False
        current_time = now or utc_now()
        exp = self.expires_at
        if exp.tzinfo is None and current_time.tzinfo is not None:
            exp = exp.replace(tzinfo=UTC)
        elif exp.tzinfo is not None and current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)
        return current_time > exp



def compute_receipt_signature(
    secret_key: str,
    decision_id: str,
    session_id: str,
    action_hash: str,
    state_hash: str,
    nonce: str,
    decision_status: DecisionStatus,
    expires_at: datetime | None = None,
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
    expires_at: datetime | None = None,
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


def verify_capability_signature(secret_key: str, capability: CapabilityPayload | dict[str, Any]) -> bool:
    """Verifica de forma inmune a ataques de temporización la autenticidad del capability token emitido."""
    if isinstance(capability, dict):
        try:
            capability = CapabilityPayload(**capability)
        except Exception:  # noqa: BLE001 - verificación fail-closed: cualquier payload inválido cuenta como capability no válida
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


def canonical_digest(value: Any) -> str:
    """SHA-256 del JSON canónico (claves ordenadas, sin espacios) de ``value``."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Listas de un snapshot de estado que se resumen elemento a elemento (hash de hashes): crecen con la sesión, y así el
# estado vivo puede cachear el hash de cada elemento en lugar de volver a serializarlos todos en cada paso (AUD-20, P-03)
_ITEMIZED_STATE_LISTS = ("steps", "evidence")


def is_state_snapshot(value: Any) -> bool:
    return isinstance(value, dict) and "session_id" in value and isinstance(value.get("steps"), list)


def state_snapshot_digest(snapshot: dict[str, Any], item_digest: Any = None) -> str:
    """Hash de un snapshot de estado (``SessionState.to_snapshot()``): cada paso y cada evidencia por separado.

    ``item_digest(obj_dict, original)`` permite reutilizar hashes ya calculados; por defecto, ``canonical_digest``.
    """
    body = dict(snapshot)
    for key in _ITEMIZED_STATE_LISTS:
        items = body.get(key) or []
        body[key] = [canonical_digest(item) if item_digest is None else item_digest(key, i, item) for i, item in enumerate(items)]
    if isinstance(body.get("forbidden_tools"), (list, set, tuple)):
        body["forbidden_tools"] = sorted(body["forbidden_tools"])
    body["state_hash_version"] = 2
    return canonical_digest(body)


def compute_state_hash(state_dict: Any) -> str:
    """Hash SHA-256 determinista del estado canónico.

    Un ``SessionState`` (o su snapshot) se resume con ``state_snapshot_digest``: el mismo valor desde el estado vivo
    (con los hashes de cada paso cacheados) que desde ``to_snapshot()``. Cualquier otro valor, con su JSON canónico.
    """
    if hasattr(state_dict, "state_digest"):
        return str(state_dict.state_digest())
    if hasattr(state_dict, "to_snapshot"):
        state_dict = state_dict.to_snapshot()
    elif hasattr(state_dict, "model_dump"):
        state_dict = state_dict.model_dump()
    elif not isinstance(state_dict, dict):
        try:
            state_dict = dict(state_dict)
        except (TypeError, ValueError):
            state_dict = {"repr": str(state_dict)}
    if is_state_snapshot(state_dict):
        return state_snapshot_digest(state_dict)
    return canonical_digest(state_dict)

