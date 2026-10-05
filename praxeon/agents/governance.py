"""Gobierno, Aislamiento de Capacidades y Contención de Privilegios Multiagente (F5-03).

Implementa las defensas de seguridad y control de ejecución entre agentes:
- AgentGovernanceGuard: Validador e interceptor de mensajes en el bus de mensajería.
- Prevención de escalación de privilegios en delegación (un agente no puede solicitar lo que no tiene permitido).
- Verificación de herramientas autorizadas (is_tool_allowed) y capabilities requeridas.
- Tolerancia a perfiles de riesgo y control de estados de ciclo de vida (ACTIVE, INACTIVE, TERMINATED).
- Generación de alertas de seguridad y registro de violaciones.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, ConfigDict, Field

from praxeon.agents.definition import AgentDefinition, AgentStatus
from praxeon.agents.protocol import AgentMessage, MessagePriority, MessageType
from praxeon.agents.registry import AgentRegistry
from praxeon.domain.models import RiskLevel


class GovernanceViolationType(str, Enum):
    """Tipos de violaciones a las políticas de gobernanza de agentes."""
    UNREGISTERED_AGENT = "UNREGISTERED_AGENT"
    INACTIVE_AGENT = "INACTIVE_AGENT"
    FORBIDDEN_TOOL = "FORBIDDEN_TOOL"
    UNAUTHORIZED_TOOL = "UNAUTHORIZED_TOOL"
    PRIVILEGE_ESCALATION = "PRIVILEGE_ESCALATION"
    MISSING_CAPABILITY = "MISSING_CAPABILITY"
    EXCEEDS_RISK_TOLERANCE = "EXCEEDS_RISK_TOLERANCE"
    TAMPERED_INTEGRITY = "TAMPERED_INTEGRITY"


class GovernanceViolation(BaseModel):
    """Registro inmutable de un intento de violación de gobernanza."""
    model_config = ConfigDict(frozen=True)

    violation_type: GovernanceViolationType
    sender_id: str
    receiver_id: str
    message_id: str
    reason: str
    severity: str = "HIGH"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GovernanceDecision(BaseModel):
    """Resultado formal de la evaluación de gobernanza para un mensaje."""
    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str
    violation: Optional[GovernanceViolation] = None


class AgentGovernanceGuard:
    """Guardia de seguridad y contención de privilegios para el bus de agentes."""

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        supervisor_id: str = "praxeon_supervisor",
        strict_mode: bool = True,
    ):
        self.registry = registry or AgentRegistry()
        self.supervisor_id = supervisor_id
        self.strict_mode = strict_mode
        self._local_definitions: Dict[str, AgentDefinition] = {}
        self._violations: List[GovernanceViolation] = []

    def register_definition(self, definition: AgentDefinition) -> None:
        """Registra o actualiza la definición de un agente para supervisión local."""
        self._local_definitions[definition.agent_id] = definition
        self.registry.register(definition)

    def get_definition(self, agent_id: str) -> Optional[AgentDefinition]:
        """Obtiene la definición activa de un agente."""
        if agent_id in self._local_definitions:
            return self._local_definitions[agent_id]
        return self.registry.get(agent_id)

    def _extract_requested_tools(self, payload: Dict[str, Any]) -> List[str]:
        """Extrae la lista de herramientas invocadas o requeridas en el cuerpo del mensaje."""
        tools: List[str] = []
        for key in ("tool", "tool_name", "requested_tool"):
            if key in payload and isinstance(payload[key], str) and payload[key].strip():
                tools.append(payload[key].strip())

        for key in ("tools", "requested_tools"):
            if key in payload and isinstance(payload[key], (list, set, tuple)):
                for t in payload[key]:
                    if isinstance(t, str) and t.strip():
                        tools.append(t.strip())

        return list(dict.fromkeys(tools))  # Eliminar duplicados preservando orden

    def _extract_required_capabilities(self, payload: Dict[str, Any]) -> List[str]:
        """Extrae las capabilities requeridas por la tarea."""
        caps: List[str] = []
        if "required_capability" in payload and isinstance(payload["required_capability"], str):
            caps.append(payload["required_capability"].strip())
        if "required_capabilities" in payload and isinstance(payload["required_capabilities"], (list, set, tuple)):
            for c in payload["required_capabilities"]:
                if isinstance(c, str) and c.strip():
                    caps.append(c.strip())
        return list(dict.fromkeys(caps))

    def validate_message(self, message: AgentMessage) -> GovernanceDecision:
        """Evalúa exhaustivamente un mensaje contra las políticas de gobernanza."""
        # 1. Verificar integridad criptográfica
        if not message.verify_integrity():
            viol = GovernanceViolation(
                violation_type=GovernanceViolationType.TAMPERED_INTEGRITY,
                sender_id=message.sender_id,
                receiver_id=message.receiver_id,
                message_id=message.message_id,
                reason="El hash de integridad del mensaje no coincide con su contenido.",
                severity="CRITICAL",
            )
            self._violations.append(viol)
            return GovernanceDecision(allowed=False, reason=viol.reason, violation=viol)

        # 2. Las alertas de seguridad dirigidas al supervisor siempre son admitidas
        if message.receiver_id == self.supervisor_id and message.message_type == MessageType.ALERT:
            return GovernanceDecision(allowed=True, reason="Alerta de seguridad dirigida al supervisor.")

        # 3. Validar estado y existencia del emisor
        if message.sender_id != self.supervisor_id:
            sender_def = self.get_definition(message.sender_id)
            if not sender_def:
                viol = GovernanceViolation(
                    violation_type=GovernanceViolationType.UNREGISTERED_AGENT,
                    sender_id=message.sender_id,
                    receiver_id=message.receiver_id,
                    message_id=message.message_id,
                    reason=f"Emisor '{message.sender_id}' no está registrado en el sistema.",
                    severity="HIGH",
                )
                self._violations.append(viol)
                return GovernanceDecision(allowed=False, reason=viol.reason, violation=viol)

            if sender_def.status != AgentStatus.ACTIVE:
                viol = GovernanceViolation(
                    violation_type=GovernanceViolationType.INACTIVE_AGENT,
                    sender_id=message.sender_id,
                    receiver_id=message.receiver_id,
                    message_id=message.message_id,
                    reason=f"Emisor '{message.sender_id}' se encuentra en estado '{sender_def.status.value}'.",
                    severity="HIGH",
                )
                self._violations.append(viol)
                return GovernanceDecision(allowed=False, reason=viol.reason, violation=viol)
        else:
            sender_def = None

        # 4. Validar destinatario (si no es broadcast ni supervisor)
        receiver_def: Optional[AgentDefinition] = None
        if message.receiver_id != "*" and message.receiver_id != self.supervisor_id:
            receiver_def = self.get_definition(message.receiver_id)
            if not receiver_def:
                viol = GovernanceViolation(
                    violation_type=GovernanceViolationType.UNREGISTERED_AGENT,
                    sender_id=message.sender_id,
                    receiver_id=message.receiver_id,
                    message_id=message.message_id,
                    reason=f"Destinatario '{message.receiver_id}' no está registrado en el sistema.",
                    severity="HIGH",
                )
                self._violations.append(viol)
                return GovernanceDecision(allowed=False, reason=viol.reason, violation=viol)

            if receiver_def.status != AgentStatus.ACTIVE:
                viol = GovernanceViolation(
                    violation_type=GovernanceViolationType.INACTIVE_AGENT,
                    sender_id=message.sender_id,
                    receiver_id=message.receiver_id,
                    message_id=message.message_id,
                    reason=f"Destinatario '{message.receiver_id}' se encuentra en estado '{receiver_def.status.value}'.",
                    severity="HIGH",
                )
                self._violations.append(viol)
                return GovernanceDecision(allowed=False, reason=viol.reason, violation=viol)

        # 5. Contención de Privilegios y Prevención de Escalación en Herramientas
        requested_tools = self._extract_requested_tools(message.payload)
        for tool in requested_tools:
            # a) Regla de No-Escalación (Privilege Containment):
            # El emisor no puede solicitar herramientas que él mismo tenga prohibidas,
            # previniendo que un agente restringido use a otro como proxy no autorizado.
            if sender_def is not None:
                has_delegation_elevation = "delegate_elevated" in sender_def.capabilities
                if not has_delegation_elevation and not sender_def.is_tool_allowed(tool):
                    viol = GovernanceViolation(
                        violation_type=GovernanceViolationType.PRIVILEGE_ESCALATION,
                        sender_id=message.sender_id,
                        receiver_id=message.receiver_id,
                        message_id=message.message_id,
                        reason=f"Intento de escalación de privilegios: emisor '{message.sender_id}' "
                               f"carece de permiso para '{tool}' y no puede delegar su ejecución.",
                        severity="CRITICAL",
                    )
                    self._violations.append(viol)
                    return GovernanceDecision(allowed=False, reason=viol.reason, violation=viol)

            # b) Regla de Autorización del Receptor:
            # El receptor debe tener permitida la herramienta.
            if receiver_def is not None:
                if not receiver_def.is_tool_allowed(tool):
                    viol = GovernanceViolation(
                        violation_type=GovernanceViolationType.FORBIDDEN_TOOL,
                        sender_id=message.sender_id,
                        receiver_id=message.receiver_id,
                        message_id=message.message_id,
                        reason=f"Herramienta prohibida/no autorizada: receptor '{message.receiver_id}' "
                               f"no tiene permitido ejecutar '{tool}'.",
                        severity="HIGH",
                    )
                    self._violations.append(viol)
                    return GovernanceDecision(allowed=False, reason=viol.reason, violation=viol)

        # 6. Validación de Capabilities requeridas
        required_caps = self._extract_required_capabilities(message.payload)
        if receiver_def is not None and required_caps:
            missing_caps = [c for c in required_caps if c not in receiver_def.capabilities]
            if missing_caps:
                viol = GovernanceViolation(
                    violation_type=GovernanceViolationType.MISSING_CAPABILITY,
                    sender_id=message.sender_id,
                    receiver_id=message.receiver_id,
                    message_id=message.message_id,
                    reason=f"Receptor '{message.receiver_id}' carece de capabilities requeridas: {missing_caps}",
                    severity="HIGH",
                )
                self._violations.append(viol)
                return GovernanceDecision(allowed=False, reason=viol.reason, violation=viol)

        # 7. Validación de Nivel de Riesgo
        risk_raw = message.payload.get("risk_level") or message.payload.get("risk")
        if receiver_def is not None and risk_raw:
            try:
                msg_risk = RiskLevel(str(risk_raw).lower())
                risk_weights = {
                    RiskLevel.LOW: 1,
                    RiskLevel.MEDIUM: 2,
                    RiskLevel.HIGH: 3,
                    RiskLevel.CRITICAL: 4,
                }
                max_tolerated = receiver_def.risk_profile.max_risk_level
                if risk_weights.get(msg_risk, 0) > risk_weights.get(max_tolerated, 0):
                    if not receiver_def.risk_profile.require_human_confirmation:
                        viol = GovernanceViolation(
                            violation_type=GovernanceViolationType.EXCEEDS_RISK_TOLERANCE,
                            sender_id=message.sender_id,
                            receiver_id=message.receiver_id,
                            message_id=message.message_id,
                            reason=f"Nivel de riesgo '{msg_risk.value}' excede la tolerancia máxima "
                                   f"'{max_tolerated.value}' del receptor sin aprobación humana activa.",
                            severity="HIGH",
                        )
                        self._violations.append(viol)
                        return GovernanceDecision(allowed=False, reason=viol.reason, violation=viol)
            except ValueError:
                pass

        return GovernanceDecision(allowed=True, reason="Mensaje conforme con las políticas de gobernanza.")

    def intercept_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Middleware para AgentMessageBus.add_interceptor. Retorna el mensaje o None si viola políticas."""
        decision = self.validate_message(message)
        if not decision.allowed:
            return None
        return message

    def get_violations(self) -> List[GovernanceViolation]:
        """Consulta el log acumulado de violaciones de gobernanza."""
        return list(self._violations)

    def clear_violations(self) -> None:
        """Limpia el log de violaciones acumuladas."""
        self._violations.clear()
