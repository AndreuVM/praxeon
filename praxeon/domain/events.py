"""Modelo de eventos inmutables para supervisión en tiempo real (praxeon/domain/events.py).

Especificación PRAXEON 1.0 (Sección 4.1 y 4.2).
Define los 15 tipos canónicos de evento, el contrato de payload estructurado,
y la secuencia monótona por sesión para streaming y replay determinista.
"""

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


def serialize_payload_value(val: Any) -> Any:
    """Convierte recursivamente estructuras complejas (datetime, UUID, Enum, BaseModel) a primitivos JSON seguros."""
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, Enum):
        return val.value
    if hasattr(val, "model_dump") and callable(val.model_dump):
        try:
            return serialize_payload_value(val.model_dump(mode="json"))
        except Exception:
            return serialize_payload_value(val.model_dump())
    if hasattr(val, "to_dict") and callable(val.to_dict):
        return serialize_payload_value(val.to_dict())
    if isinstance(val, dict):
        return {str(k): serialize_payload_value(v) for k, v in val.items()}
    if isinstance(val, (list, tuple, set)):
        return [serialize_payload_value(v) for v in val]
    return str(val)


class EventType(str, Enum):
    """Los 15 tipos canónicos de eventos emitidos por el runtime PRAXEON (Sección 4.1)."""
    SESSION_STARTED = "session.started"
    GOAL_CREATED = "goal.created"
    ACTION_PROPOSED = "action.proposed"
    EVIDENCE_EVALUATED = "evidence.evaluated"
    RISK_ASSESSED = "risk.assessed"
    PROVIDER_EVALUATED = "provider.evaluated"
    POLICY_DECIDED = "policy.decided"
    CAPABILITY_ISSUED = "capability.issued"
    EXECUTION_STARTED = "execution.started"
    EXECUTION_COMPLETED = "execution.completed"
    OBSERVATION_RECORDED = "observation.recorded"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_COMPLETED = "approval.completed"
    DECISION_PRUNED = "decision.pruned"
    SESSION_COMPLETED = "session.completed"


class RuntimeEvent(BaseModel):
    """Contrato formal de evento persistible y reproducible (Sección 4.2)."""
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    session_id: str
    sequence: int  # Secuencia monótona estrictamente creciente por sesión: 1, 2, 3...
    type: EventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    node_id: Optional[str] = None
    parent_id: Optional[str] = None
    decision_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialización canónica para JSON / WebSocket."""
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "type": self.type.value if isinstance(self.type, EventType) else str(self.type),
            "timestamp": self.timestamp.isoformat() if hasattr(self.timestamp, "isoformat") else str(self.timestamp),
            "node_id": self.node_id,
            "parent_id": self.parent_id,
            "decision_id": self.decision_id,
            "payload": serialize_payload_value(self.payload),
        }


# =====================================================================
# Factory Helpers para garantizar integridad de tipos y payloads
# =====================================================================

def make_event(
    session_id: str,
    sequence: int,
    event_type: EventType,
    node_id: Optional[str] = None,
    parent_id: Optional[str] = None,
    decision_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
    timestamp: Optional[datetime] = None,
) -> RuntimeEvent:
    """Crea una instancia inmutable de RuntimeEvent validando secuencia y tipos."""
    return RuntimeEvent(
        event_id=event_id or f"evt_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        sequence=sequence,
        type=event_type,
        timestamp=timestamp or datetime.utcnow(),
        node_id=node_id,
        parent_id=parent_id,
        decision_id=decision_id,
        payload=payload or {},
    )
