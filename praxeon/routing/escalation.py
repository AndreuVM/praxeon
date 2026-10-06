"""Mecanismo de Escalado Dinámico Supervisado (Dynamic Escalation) (F7-04).

Implementa la supervisión y escalado adaptativo de misiones de agentes:
- Inicialización económica o restringida (agente ligero o rol primario).
- Detección de fallos de ejecución, incertidumbre epistémica, bucles de herramientas
  o violaciones de políticas de contención.
- Escalado determinista hacia modelos superiores (Flash -> Pro), especialistas de dominio
  (Developer -> Security Auditor) o suspensión formal para intervención humana (HITL).
"""

from datetime import datetime, timezone
from enum import Enum
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from praxeon.agents.bus import AgentMessageBus
from praxeon.agents.definition import AgentDefinition, AgentStatus
from praxeon.agents.protocol import AgentMessage, MessagePriority, MessageType
from praxeon.agents.registry import AgentRegistry
from praxeon.routing.models import (
    RoutingDecision,
    RoutingStrategyType,
    TaskComplexity,
    TaskRequirement,
)
from praxeon.routing.router import AgentRouter


class EscalationTriggerType(str, Enum):
    """Causas canónicas que disparan el escalado dinámico."""
    UNCERTAINTY = "UNCERTAINTY"              # Incertidumbre epistémica elevada (> 0.7)
    TEST_FAILURE = "TEST_FAILURE"            # Fallo repetido de pruebas unitarias o sintaxis
    TOOL_LOOP = "TOOL_LOOP"                  # Detección de bucle o estancamiento de herramientas
    SECURITY_VIOLATION = "SECURITY_VIOLATION"# Violación de política de contención o riesgo
    TIMEOUT = "TIMEOUT"                      # Límite de pasos o tiempo excedido
    MANUAL_REQUEST = "MANUAL_REQUEST"        # Petición explícita del agente o supervisor


class EscalationContext:
    """Contexto operativo del fallo que motiva el análisis de escalado."""

    def __init__(
        self,
        task: TaskRequirement,
        current_decision: RoutingDecision,
        trigger_type: EscalationTriggerType,
        failure_reason: str,
        attempt_count: int = 1,
        logs_or_trace: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.task = task
        self.current_decision = current_decision
        self.trigger_type = trigger_type
        self.failure_reason = failure_reason
        self.attempt_count = attempt_count
        self.logs_or_trace = logs_or_trace or []
        self.metadata = metadata or {}


class EscalationResult:
    """Resultado formal de la evaluación de escalado dinámico."""

    def __init__(
        self,
        escalated: bool,
        escalation_level: int,
        escalation_type: str,
        rationale: str,
        new_decision: Optional[RoutingDecision] = None,
        requires_human: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.escalated = escalated
        self.escalation_level = escalation_level
        self.escalation_type = escalation_type  # MODEL_UPGRADE, ROLE_SPECIALIZATION, HUMAN_INTERVENTION, NONE
        self.rationale = rationale
        self.new_decision = new_decision
        self.requires_human = requires_human
        self.metadata = metadata or {}


class DynamicEscalationEngine:
    """Motor de escalado supervisado determinista y adaptativo."""

    def __init__(
        self,
        router: AgentRouter,
        max_escalation_level: int = 3,
    ):
        self.router = router
        self.max_escalation_level = max_escalation_level

    def evaluate_escalation(self, context: EscalationContext) -> EscalationResult:
        """Evalúa las condiciones de fallo y determina el siguiente nivel de escalado."""
        curr_level = context.current_decision.escalation_level
        next_level = curr_level + 1

        # 1. Si se ha alcanzado el límite máximo de escalados automáticos -> Intervención humana
        if next_level > self.max_escalation_level or context.attempt_count >= self.max_escalation_level:
            return EscalationResult(
                escalated=True,
                escalation_level=next_level,
                escalation_type="HUMAN_INTERVENTION",
                rationale=(
                    f"Superado el umbral máximo de escalado ({self.max_escalation_level} niveles/intentos). "
                    f"Se suspende la tarea '{context.task.task_id}' para revisión humana obligatoria: {context.failure_reason}"
                ),
                new_decision=None,
                requires_human=True,
                metadata={"trigger": context.trigger_type.value, "last_agent": context.current_decision.selected_agent_id},
            )

        # 2. Filtrar agentes elegibles excluyendo el agente que acaba de fallar
        eligible = self.router.filter_eligible_agents(context.task)
        candidates = [ag for ag in eligible if ag.agent_id != context.current_decision.selected_agent_id]

        # 3. Estrategia según el tipo de disparador
        if context.trigger_type == EscalationTriggerType.SECURITY_VIOLATION:
            # Escalar prioritariamente hacia auditores de seguridad o compliance
            sec_agents = [ag for ag in candidates if "security_audit" in ag.capabilities or "compliance_check" in ag.capabilities]
            target_agent = sec_agents[0] if sec_agents else (candidates[0] if candidates else None)
            escalation_type = "ROLE_SPECIALIZATION"
            rationale_prefix = "Escalado por violación de seguridad/política hacia auditor especializado"

        elif context.trigger_type in [EscalationTriggerType.TEST_FAILURE, EscalationTriggerType.TOOL_LOOP]:
            # Escalar hacia agentes con modelos Pro / mayor capacidad de razonamiento
            pro_agents = [ag for ag in candidates if "pro" in ag.model.model_name.lower() or "ultra" in ag.model.model_name.lower()]
            target_agent = pro_agents[0] if pro_agents else (candidates[0] if candidates else None)
            escalation_type = "MODEL_UPGRADE"
            rationale_prefix = f"Escalado por {context.trigger_type.value} hacia modelo de mayor capacidad de razonamiento"

        else:
            # UNCERTAINTY u otros: seleccionar el candidato con mayor score de capabilities
            candidates.sort(
                key=lambda ag: len([c for c in context.task.required_capabilities if c in ag.capabilities]),
                reverse=True,
            )
            target_agent = candidates[0] if candidates else None
            escalation_type = "ROLE_SPECIALIZATION"
            rationale_prefix = f"Escalado por {context.trigger_type.value} hacia especialista alternativo"

        # 4. Si no hay agentes alternativos elegibles en el registro -> Suspender para humano
        if not target_agent:
            return EscalationResult(
                escalated=True,
                escalation_level=next_level,
                escalation_type="HUMAN_INTERVENTION",
                rationale=(
                    f"No existen agentes alternativos elegibles para mitigar {context.trigger_type.value}. "
                    f"Requiere intervención humana para la tarea '{context.task.task_id}'."
                ),
                new_decision=None,
                requires_human=True,
            )

        # 5. Generar nueva decisión formal de enrutamiento con nivel de escalado incrementado
        new_decision = RoutingDecision(
            decision_id=f"rd_esc_{uuid.uuid4().hex[:12]}",
            task_id=context.task.task_id,
            selected_agent_id=target_agent.agent_id,
            confidence=min(0.99, max(0.70, context.current_decision.confidence + 0.1)),
            strategy_used=RoutingStrategyType.ADAPTIVE,
            alternative_agent_ids=[a.agent_id for a in candidates if a.agent_id != target_agent.agent_id],
            rationale=f"{rationale_prefix}: '{target_agent.name}' ({target_agent.role}). Causa previa: {context.failure_reason}",
            matched_capabilities=[c for c in context.task.required_capabilities if c in target_agent.capabilities],
            escalation_level=next_level,
            metadata={
                "previous_agent_id": context.current_decision.selected_agent_id,
                "escalation_trigger": context.trigger_type.value,
                "failure_reason": context.failure_reason,
            },
        )
        self.router._decision_history.append(new_decision)

        return EscalationResult(
            escalated=True,
            escalation_level=next_level,
            escalation_type=escalation_type,
            rationale=new_decision.rationale,
            new_decision=new_decision,
            requires_human=False,
            metadata=new_decision.metadata,
        )

    def escalate_and_dispatch(
        self,
        context: EscalationContext,
        bus: AgentMessageBus,
        session_id: str = "escalated_session",
    ) -> Tuple[EscalationResult, Optional[AgentMessage]]:
        """Evalúa el escalado y despacha automáticamente la tarea al buzón del nuevo agente si procede."""
        result = self.evaluate_escalation(context)

        if not result.escalated or result.requires_human or not result.new_decision:
            return result, None

        # Despachar mensaje de alta prioridad con contexto del fallo previo
        msg = AgentMessage(
            message_id=f"msg_esc_{context.task.task_id}_{uuid.uuid4().hex[:8]}",
            sender_id="supervisor_escalation_engine",
            receiver_id=result.new_decision.selected_agent_id,
            session_id=session_id,
            message_type=MessageType.DELEGATE,
            priority=MessagePriority.HIGH,
            task_id=context.task.task_id,
            payload={
                "prompt": context.task.prompt,
                "escalation_level": result.escalation_level,
                "previous_agent_id": context.current_decision.selected_agent_id,
                "trigger": context.trigger_type.value,
                "failure_reason": context.failure_reason,
            },
        )
        bus.send(msg)

        return result, msg
