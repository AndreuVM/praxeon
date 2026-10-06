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
import hmac
import json
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field, computed_field


class MessageType(str, Enum):
    """Categorías funcionales de mensajes en la topología de comunicación."""
    REQUEST = "REQUEST"          # Consulta o solicitud directa de acción
    RESPONSE = "RESPONSE"        # Respuesta a una solicitud o delegación previa
    DELEGATE = "DELEGATE"        # Delegación supervisada de tarea de un orquestador a un agente
    BROADCAST = "BROADCAST"      # Notificación de difusión abierta a todos los agentes de la sesión
    ALERT = "ALERT"              # Notificación urgente de violación de política, riesgo o anomalía
    HEARTBEAT = "HEARTBEAT"      # Telemetría de vivacidad y estado operativo
    AGENT_REQUEST = "AGENT_REQUEST"  # Solicitud formal tipada entre agentes
    AGENT_RESULT = "AGENT_RESULT"    # Resultado estructurado de ejecución
    AGENT_ACK = "AGENT_ACK"          # Confirmación positiva de recepción / aceptación
    AGENT_NACK = "AGENT_NACK"        # Confirmación negativa / rechazo de procesamiento
    ACK = "AGENT_ACK"
    NACK = "AGENT_NACK"
    RESULT = "AGENT_RESULT"


class MessagePriority(IntEnum):
    """Niveles de prioridad para ordenación en buzones y colas."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


class AgentMessage(BaseModel):
    """Mensaje estructurado e inmutable entre agentes en PRAXEON con verificación criptográfica."""
    model_config = ConfigDict(frozen=True)

    message_id: str = Field(description="Identificador único del mensaje")
    sender_id: str = Field(description="ID del agente emisor o 'praxeon_supervisor'")
    receiver_id: str = Field(description="ID del agente destinatario o '*' para broadcast")
    session_id: str = Field(description="Sesión de supervisión a la que pertenece la interacción")
    task_id: Optional[str] = Field(default=None, description="Identificador de la tarea asociada si aplica")
    message_type: MessageType = Field(default=MessageType.REQUEST)
    priority: MessagePriority = Field(default=MessagePriority.NORMAL)
    correlation_id: Optional[str] = Field(default=None, description="ID del mensaje al que responde este mensaje")
    in_reply_to: Optional[str] = Field(default=None, description="ID directo del mensaje de solicitud previa")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Contenido de datos o directiva")
    evidence_refs: List[str] = Field(default_factory=list, description="IDs de evidencias de SessionState respaldatorias")
    context_snapshot_fingerprint: Optional[str] = Field(default=None, description="Huella de contexto L1/L2 vinculada")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    received_integrity_hash: Optional[str] = Field(default=None, description="Hash de integridad emitido por el remitente o recibido en la red")
    signature: Optional[str] = Field(default=None, description="Firma HMAC-SHA256 para autenticación de origen y no repudio")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Inicializa received_integrity_hash con el hash calculado si no fue provisto explícitamente."""
        if self.correlation_id is None and self.in_reply_to is not None:
            object.__setattr__(self, "correlation_id", self.in_reply_to)
        if self.received_integrity_hash is None:
            object.__setattr__(self, "received_integrity_hash", self.computed_integrity_hash)

    def canonical_bytes(self) -> bytes:
        """Serializa de forma determinista los campos invariantes del mensaje para hashing y firma."""
        canonical_dict = {
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "receiver_id": self.receiver_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "message_type": self.message_type.value if hasattr(self.message_type, "value") else str(self.message_type),
            "priority": int(self.priority),
            "correlation_id": self.correlation_id,
            "in_reply_to": self.in_reply_to,
            "payload": self.payload,
            "evidence_refs": sorted(self.evidence_refs),
            "context_snapshot_fingerprint": self.context_snapshot_fingerprint,
            "timestamp": self.timestamp.isoformat(),
        }
        return json.dumps(canonical_dict, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")


    @computed_field
    @property
    def computed_integrity_hash(self) -> str:
        """Calcula el hash SHA-256 canónico completo (64 caracteres) del mensaje."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @computed_field
    @property
    def integrity_hash(self) -> str:
        """Hash de integridad truncado canónico (16 caracteres) para compatibilidad histórica."""
        return self.computed_integrity_hash[:16]

    def verify_integrity(self, expected_hash: Optional[str] = None) -> bool:
        """Verifica la correspondencia criptográfica del hash contra alteración del payload o cabeceras."""
        target = expected_hash or self.received_integrity_hash or self.metadata.get("expected_hash") or self.metadata.get("integrity_hash")
        if not target:
            return False
        if len(target) == 16:
            return hmac.compare_digest(self.computed_integrity_hash[:16], target)
        return hmac.compare_digest(self.computed_integrity_hash, target)

    def compute_hmac(self, secret: str | bytes) -> str:
        """Calcula la firma HMAC-SHA256 del contenido canónico utilizando una clave secreta compartida."""
        key = secret.encode("utf-8") if isinstance(secret, str) else secret
        return hmac.new(key, self.canonical_bytes(), hashlib.sha256).hexdigest()

    def sign(self, secret: str | bytes) -> "AgentMessage":
        """Devuelve una nueva copia del mensaje firmada criptográficamente con HMAC-SHA256."""
        sig = self.compute_hmac(secret)
        dump = self.model_dump(exclude={"computed_integrity_hash", "integrity_hash"})
        dump["received_integrity_hash"] = self.computed_integrity_hash
        dump["signature"] = sig
        return AgentMessage(**dump)

    def verify_signature(self, secret: str | bytes) -> bool:
        """Verifica si la firma HMAC-SHA256 del mensaje es auténtica y válida para la clave provista."""
        if not self.signature:
            return False
        expected_sig = self.compute_hmac(secret)
        return hmac.compare_digest(self.signature, expected_sig)

    def create_response(
        self,
        sender_id: str,
        payload: Dict[str, Any],
        message_id: Optional[str] = None,
        priority: Optional[MessagePriority] = None,
        evidence_refs: Optional[List[str]] = None,
    ) -> "AgentMessage":
        """Genera una respuesta formal referenciando este mensaje como correlation_id."""
        msg_id = message_id or f"msg_resp_{uuid.uuid4().hex[:12]}"
        return AgentMessage(
            message_id=msg_id,
            sender_id=sender_id,
            receiver_id=self.sender_id,
            session_id=self.session_id,
            task_id=self.task_id,
            message_type=MessageType.RESPONSE,
            priority=priority or self.priority,
            correlation_id=self.correlation_id or self.message_id,
            in_reply_to=self.message_id,
            payload=payload,
            evidence_refs=evidence_refs or list(self.evidence_refs),
            context_snapshot_fingerprint=self.context_snapshot_fingerprint,
        )

    def create_ack(
        self,
        sender_id: str,
        message_id: Optional[str] = None,
        note: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AgentMessage":
        """Genera una confirmación formal positiva (ACK) en respuesta a este mensaje."""
        msg_id = message_id or f"msg_ack_{uuid.uuid4().hex[:12]}"
        payload = {"status": "ACK", "acknowledged_message_id": self.message_id}
        if note:
            payload["note"] = note
        return AgentMessage(
            message_id=msg_id,
            sender_id=sender_id,
            receiver_id=self.sender_id,
            session_id=self.session_id,
            task_id=self.task_id,
            message_type=MessageType.AGENT_ACK,
            priority=MessagePriority.HIGH,
            correlation_id=self.correlation_id or self.message_id,
            in_reply_to=self.message_id,
            payload=payload,
            metadata=metadata or {},
        )

    def create_nack(
        self,
        sender_id: str,
        reason: str,
        error_code: Optional[str] = None,
        message_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AgentMessage":
        """Genera una confirmación formal negativa (NACK) / rechazo en respuesta a este mensaje."""
        msg_id = message_id or f"msg_nack_{uuid.uuid4().hex[:12]}"
        payload = {
            "status": "NACK",
            "rejected_message_id": self.message_id,
            "reason": reason,
        }
        if error_code:
            payload["error_code"] = error_code
        return AgentMessage(
            message_id=msg_id,
            sender_id=sender_id,
            receiver_id=self.sender_id,
            session_id=self.session_id,
            task_id=self.task_id,
            message_type=MessageType.AGENT_NACK,
            priority=MessagePriority.HIGH,
            correlation_id=self.correlation_id or self.message_id,
            in_reply_to=self.message_id,
            payload=payload,
            metadata=metadata or {},
        )

    def create_result(
        self,
        sender_id: str,
        payload: Dict[str, Any],
        message_id: Optional[str] = None,
        priority: Optional[MessagePriority] = None,
        evidence_refs: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AgentMessage":
        """Genera un resultado formal estructurado en respuesta a una solicitud o tarea previa."""
        msg_id = message_id or f"msg_res_{uuid.uuid4().hex[:12]}"
        return AgentMessage(
            message_id=msg_id,
            sender_id=sender_id,
            receiver_id=self.sender_id,
            session_id=self.session_id,
            task_id=self.task_id,
            message_type=MessageType.AGENT_RESULT,
            priority=priority or self.priority,
            correlation_id=self.correlation_id or self.message_id,
            in_reply_to=self.message_id,
            payload=payload,
            evidence_refs=evidence_refs or list(self.evidence_refs),
            context_snapshot_fingerprint=self.context_snapshot_fingerprint,
            metadata=metadata or {},
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
        msg_id = message_id or f"msg_del_{uuid.uuid4().hex[:12]}"
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
        """Serialización completa a diccionario con trazabilidad de hashes y firma."""
        dump = self.model_dump(mode="json")
        dump["integrity_hash"] = self.integrity_hash
        dump["computed_integrity_hash"] = self.computed_integrity_hash
        dump["received_integrity_hash"] = self.received_integrity_hash or self.computed_integrity_hash
        dump["signature"] = self.signature
        return dump

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentMessage":
        """Reconstruye un mensaje desde un diccionario serializado preservando hashes."""
        clean = dict(data)
        legacy_hash = clean.pop("integrity_hash", None)
        computed_in_data = clean.pop("computed_integrity_hash", None)
        if "received_integrity_hash" not in clean or clean["received_integrity_hash"] is None:
            clean["received_integrity_hash"] = computed_in_data or legacy_hash
        if isinstance(clean.get("timestamp"), str):
            clean["timestamp"] = datetime.fromisoformat(clean["timestamp"])
        return cls(**clean)
