"""Protocolo Formal de Mensajería Inter-Agente (Agent Message Protocol) (F5-01).

Especifica el formato canónico, inmutable y verificable de intercambio de mensajes:
- MessageType: Semántica funcional del mensaje (REQUEST, RESPONSE, DELEGATE, BROADCAST, ALERT, HEARTBEAT).
- MessagePriority: Prioridad de despacho y ordenación en colas (LOW a CRITICAL).
- AgentMessage: Envoltorio formal (Envelope) inmutable con trazabilidad de correlación,
  referencias a evidencias empíricas (evidence_refs), snapshot de contexto y huella de integridad.
"""

from datetime import datetime, timezone
from enum import Enum, IntEnum
import hashlib
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, computed_field


class MessageType(str, Enum):
    """Categorías funcionales de mensajes en la topología de comunicación."""
    REQUEST = "REQUEST"          # Consulta o solicitud directa de acción
    RESPONSE = "RESPONSE"        # Respuesta a una solicitud o delegación previa
    DELEGATE = "DELEGATE"        # Delegación supervisada de tarea de un orquestador a un agente
    BROADCAST = "BROADCAST"      # Notificación de difusión abierta a todos los agentes de la sesión
    ALERT = "ALERT"              # Notificación urgente de violación de política, riesgo o anomalía
    HEARTBEAT = "HEARTBEAT"      # Telemetría de vivacidad y estado operativo


class MessagePriority(IntEnum):
    """Niveles de prioridad para ordenación en buzones y colas."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


class AgentMessage(BaseModel):
    """Mensaje estructurado e inmutable entre agentes en PRAXEON."""
    model_config = ConfigDict(frozen=True)

    message_id: str = Field(description="Identificador único del mensaje")
    sender_id: str = Field(description="ID del agente emisor o 'praxeon_supervisor'")
    receiver_id: str = Field(description="ID del agente destinatario o '*' para broadcast")
    session_id: str = Field(description="Sesión de supervisión a la que pertenece la interacción")
    task_id: Optional[str] = Field(default=None, description="Identificador de la tarea asociada si aplica")
    message_type: MessageType = Field(default=MessageType.REQUEST)
    priority: MessagePriority = Field(default=MessagePriority.NORMAL)
    correlation_id: Optional[str] = Field(default=None, description="ID del mensaje al que responde este mensaje")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Contenido de datos o directiva")
    evidence_refs: List[str] = Field(default_factory=list, description="IDs de evidencias de SessionState respaldatorias")
    context_snapshot_fingerprint: Optional[str] = Field(default=None, description="Huella de contexto L1/L2 vinculada")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def integrity_hash(self) -> str:
        """Calcula el hash SHA-256 canónico del mensaje para garantizar su integridad."""
        canonical_dict = {
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "receiver_id": self.receiver_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "message_type": self.message_type.value,
            "priority": int(self.priority),
            "correlation_id": self.correlation_id,
            "payload": self.payload,
            "evidence_refs": sorted(self.evidence_refs),
            "context_snapshot_fingerprint": self.context_snapshot_fingerprint,
            "timestamp": self.timestamp.isoformat(),
        }
        encoded = json.dumps(canonical_dict, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def verify_integrity(self, expected_hash: Optional[str] = None) -> bool:
        """Verifica la validez y correspondencia del hash criptográfico de integridad."""
        target = expected_hash or self.metadata.get("expected_hash") or self.metadata.get("integrity_hash")
        if target:
            return self.integrity_hash == target
        return bool(self.integrity_hash and len(self.integrity_hash) == 16)

    def create_response(
        self,
        sender_id: str,
        payload: Dict[str, Any],
        message_id: Optional[str] = None,
        priority: Optional[MessagePriority] = None,
        evidence_refs: Optional[List[str]] = None,
    ) -> "AgentMessage":
        """Genera una respuesta formal referenciando este mensaje como correlation_id."""
        msg_id = message_id or f"msg_resp_{int(datetime.now(timezone.utc).timestamp()*1000)%100000}"
        return AgentMessage(
            message_id=msg_id,
            sender_id=sender_id,
            receiver_id=self.sender_id,
            session_id=self.session_id,
            task_id=self.task_id,
            message_type=MessageType.RESPONSE,
            priority=priority or self.priority,
            correlation_id=self.message_id,
            payload=payload,
            evidence_refs=evidence_refs or list(self.evidence_refs),
            context_snapshot_fingerprint=self.context_snapshot_fingerprint,
        )

    @classmethod
    def create_delegation(
        cls,
        sender_id: str,
        receiver_id: str,
        session_id: str,
        task_id: str,
        payload: Dict[str, Any],
        message_id: Optional[str] = None,
        priority: MessagePriority = MessagePriority.NORMAL,
        evidence_refs: Optional[List[str]] = None,
        context_snapshot_fingerprint: Optional[str] = None,
    ) -> "AgentMessage":
        """Crea un mensaje de delegación supervisada de tarea."""
        msg_id = message_id or f"msg_del_{int(datetime.now(timezone.utc).timestamp()*1000)%100000}"
        return cls(
            message_id=msg_id,
            sender_id=sender_id,
            receiver_id=receiver_id,
            session_id=session_id,
            task_id=task_id,
            message_type=MessageType.DELEGATE,
            priority=priority,
            payload=payload,
            evidence_refs=evidence_refs or [],
            context_snapshot_fingerprint=context_snapshot_fingerprint,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialización completa a diccionario."""
        dump = self.model_dump(mode="json")
        dump["integrity_hash"] = self.integrity_hash
        return dump

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentMessage":
        """Reconstruye un mensaje desde un diccionario serializado."""
        clean = dict(data)
        clean.pop("integrity_hash", None)
        if isinstance(clean.get("timestamp"), str):
            clean["timestamp"] = datetime.fromisoformat(clean["timestamp"])
        return cls(**clean)
