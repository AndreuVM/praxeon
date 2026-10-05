"""Módulo Central de Enrutamiento Inteligente de Agentes (AgentRouter) (F7-01).

Implementa la asignación determinista y semántica de tareas hacia agentes especializados:
- AgentRouter:
    * Filtrado estricto de elegibilidad según capacidades, contención de privilegios y tolerancia de riesgo.
    * Estrategias de enrutamiento conmutables (MANUAL, STATIC, RULE_BASED, SEMANTIC, COST_AWARE).
    * Despacho coordinado mediante AgentMessageBus hacia los buzones dedicados de los agentes.
    * Registro histórico auditable de decisiones de ruteo con scores de confianza.
"""

from datetime import datetime, timezone
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from praxeon.agents.bus import AgentMessageBus
from praxeon.agents.definition import AgentDefinition, AgentStatus
from praxeon.agents.protocol import AgentMessage, MessagePriority, MessageType
from praxeon.agents.registry import AgentRegistry
from praxeon.domain.assessment import RiskLevel
from praxeon.routing.models import (
    RoutingDecision,
    RoutingStrategyType,
    TaskComplexity,
    TaskRequirement,
)
from praxeon.routing.strategies import (
    AdaptiveRoutingStrategy,
    CostAwareRoutingStrategy,
    SemanticRoutingStrategy,
)


class AgentRouter:
    """Enrutador central para despacho de misiones hacia agentes idóneos."""

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        default_strategy: RoutingStrategyType = RoutingStrategyType.RULE_BASED,
    ):
        self.registry = registry or AgentRegistry()
        self.default_strategy = default_strategy
        self._strategy_handlers: Dict[
            RoutingStrategyType,
            Callable[[TaskRequirement, List[AgentDefinition]], RoutingDecision],
        ] = {}
        self._decision_history: List[RoutingDecision] = []

        # Instanciar y registrar estrategias canónicas por defecto
        self.cost_strategy = CostAwareRoutingStrategy()
        self.semantic_strategy = SemanticRoutingStrategy()
        self.adaptive_strategy = AdaptiveRoutingStrategy()

        self.register_strategy(RoutingStrategyType.RULE_BASED, self._route_rule_based)
        self.register_strategy(RoutingStrategyType.STATIC, self._route_static)
        self.register_strategy(RoutingStrategyType.COST_AWARE, self.cost_strategy.route)
        self.register_strategy(RoutingStrategyType.SEMANTIC, self.semantic_strategy.route)
        self.register_strategy(RoutingStrategyType.ADAPTIVE, self.adaptive_strategy.route)

    def register_strategy(
        self,
        strategy_type: RoutingStrategyType,
        handler: Callable[[TaskRequirement, List[AgentDefinition]], RoutingDecision],
    ) -> None:
        """Registra un ejecutor de estrategia de enrutamiento personalizada."""
        self._strategy_handlers[strategy_type] = handler

    def filter_eligible_agents(self, task: TaskRequirement) -> List[AgentDefinition]:
        """Filtra y retorna únicamente los agentes activos que satisfacen las restricciones de la tarea."""
        all_agents = self.registry.list_all(status=AgentStatus.ACTIVE)
        eligible: List[AgentDefinition] = []

        risk_weights = {
            RiskLevel.LOW: 1,
            RiskLevel.MEDIUM: 2,
            RiskLevel.HIGH: 3,
            RiskLevel.CRITICAL: 4,
        }
        task_risk_val = risk_weights.get(task.inferred_risk, 1)

        for ag in all_agents:
            # 1. Validar que no tenga prohibida ninguna de las herramientas requeridas
            if any(not ag.is_tool_allowed(tool) for tool in task.required_tools):
                continue

            # 2. Validar tolerancia a riesgo del agente
            ag_risk_val = risk_weights.get(ag.risk_profile.max_risk_level, 2)
            if task_risk_val > ag_risk_val and not ag.risk_profile.require_human_confirmation:
                continue

            # 3. Validar capabilities obligatorias
            if task.required_capabilities:
                if not all(c in ag.capabilities for c in task.required_capabilities):
                    continue

            eligible.append(ag)

        return eligible

    def _route_static(self, task: TaskRequirement, eligible: List[AgentDefinition]) -> RoutingDecision:
        """Estrategia estática: selecciona el primer agente disponible por orden alfabético/ID."""
        if not eligible:
            raise ValueError(f"No hay agentes elegibles para la tarea '{task.task_id}'.")

        selected = sorted(eligible, key=lambda a: a.agent_id)[0]
        alts = [a.agent_id for a in eligible if a.agent_id != selected.agent_id]

        return RoutingDecision(
            decision_id=f"rd_static_{int(time.time()*1000)%100000}",
            task_id=task.task_id,
            selected_agent_id=selected.agent_id,
            confidence=0.8,
            strategy_used=RoutingStrategyType.STATIC,
            alternative_agent_ids=alts[:3],
            rationale=f"Asignación estática determinista hacia '{selected.name}' ({selected.role}).",
            matched_capabilities=[c for c in task.required_capabilities if c in selected.capabilities],
        )

    def _route_rule_based(self, task: TaskRequirement, eligible: List[AgentDefinition]) -> RoutingDecision:
        """Estrategia basada en reglas: puntúa agentes según correspondencia de rol, capabilities y skills."""
        if not eligible:
            raise ValueError(f"No hay agentes elegibles que cumplan las restricciones para '{task.task_id}'.")

        scored_agents: List[Tuple[float, AgentDefinition]] = []
        prompt_lower = task.prompt.lower()

        for ag in eligible:
            score = 1.0

            # Bonificación por match en capabilities
            matched_caps = [c for c in task.required_capabilities if c in ag.capabilities]
            score += len(matched_caps) * 2.0

            # Bonificación por correspondencia semántica del rol con el prompt
            role_terms = ag.role.lower().split()
            if any(term in prompt_lower for term in role_terms):
                score += 3.0

            # Bonificación por skills específicos
            for sk in ag.skills:
                if sk.lower().replace("-", " ") in prompt_lower:
                    score += 1.5

            # Penalización suave si el modelo es excesivamente pesado para tareas triviales
            if task.complexity == TaskComplexity.TRIVIAL and "pro" in ag.model.model_name.lower():
                score -= 0.5

            scored_agents.append((score, ag))

        # Ordenar descendentemente por score
        scored_agents.sort(key=lambda item: item[0], reverse=True)
        top_score, top_agent = scored_agents[0]

        # Calibración de confianza [0.5, 0.99]
        matched_caps_count = len([c for c in task.required_capabilities if c in top_agent.capabilities])
        req_caps_count = len(task.required_capabilities)
        cap_ratio = (matched_caps_count / req_caps_count) if req_caps_count > 0 else 1.0

        base_conf = 0.7 * cap_ratio
        affinity_bonus = min(0.28, max(0.0, (top_score - 1.0) * 0.05))
        norm_confidence = min(0.99, max(0.5, round(base_conf + affinity_bonus, 3)))
        alternatives = [a.agent_id for _, a in scored_agents[1:4]]

        return RoutingDecision(
            decision_id=f"rd_rule_{int(time.time()*1000)%100000}",
            task_id=task.task_id,
            selected_agent_id=top_agent.agent_id,
            confidence=norm_confidence,
            strategy_used=RoutingStrategyType.RULE_BASED,
            alternative_agent_ids=alternatives,
            rationale=(
                f"Selección por reglas óptimas: '{top_agent.name}' (Rol: {top_agent.role}) "
                f"con score de afinidad {round(top_score, 2)}."
            ),
            matched_capabilities=[c for c in task.required_capabilities if c in top_agent.capabilities],
        )

    def route(
        self,
        task: TaskRequirement,
        strategy: Optional[RoutingStrategyType] = None,
        manual_agent_id: Optional[str] = None,
    ) -> RoutingDecision:
        """Determina el agente óptimo para la tarea según la estrategia elegida."""
        active_strat = strategy or self.default_strategy

        # Manejo de asignación manual explícita
        if manual_agent_id or active_strat == RoutingStrategyType.MANUAL:
            target_id = manual_agent_id or task.metadata.get("target_agent_id")
            if not target_id:
                raise ValueError("Estrategia MANUAL requiere especificar 'manual_agent_id'.")
            agent = self.registry.get(target_id)
            if not agent:
                raise ValueError(f"Agente manual '{target_id}' no encontrado en el registro.")
            if agent.status != AgentStatus.ACTIVE:
                raise ValueError(f"Agente manual '{target_id}' no se encuentra en estado ACTIVE.")

            decision = RoutingDecision(
                decision_id=f"rd_man_{int(time.time()*1000)%100000}",
                task_id=task.task_id,
                selected_agent_id=agent.agent_id,
                confidence=1.0,
                strategy_used=RoutingStrategyType.MANUAL,
                rationale=f"Asignación manual forzada por el operador hacia '{agent.name}'.",
                matched_capabilities=[c for c in task.required_capabilities if c in agent.capabilities],
            )
            self._decision_history.append(decision)
            return decision

        eligible = self.filter_eligible_agents(task)
        if not eligible:
            raise ValueError(
                f"Ningún agente registrado cumple los requisitos de capacidades, "
                f"herramientas y riesgo para la tarea '{task.task_id}'."
            )

        handler = self._strategy_handlers.get(active_strat)
        if not handler:
            # Fallback a rule-based
            handler = self._route_rule_based

        decision = handler(task, eligible)
        self._decision_history.append(decision)
        return decision

    def dispatch(
        self,
        task: TaskRequirement,
        bus: AgentMessageBus,
        session_id: str = "default_session",
        strategy: Optional[RoutingStrategyType] = None,
        manual_agent_id: Optional[str] = None,
    ) -> Tuple[RoutingDecision, AgentMessage]:
        """Toma la decisión de enrutamiento y despacha el mensaje formal al buzón del agente."""
        decision = self.route(task=task, strategy=strategy, manual_agent_id=manual_agent_id)

        msg = AgentMessage(
            message_id=f"msg_route_{task.task_id}_{int(time.time()*1000)%10000}",
            sender_id="praxeon_supervisor",
            receiver_id=decision.selected_agent_id,
            session_id=session_id,
            task_id=task.task_id,
            message_type=MessageType.DELEGATE,
            priority=task.priority,
            payload={
                "prompt": task.prompt,
                "complexity": task.complexity.value,
                "required_tools": task.required_tools,
                "target_files": task.target_files,
                "confidence": decision.confidence,
                "routing_decision_id": decision.decision_id,
            },
        )
        bus.send(msg)
        return decision, msg

    def get_history(self) -> List[RoutingDecision]:
        """Consulta el log acumulado de decisiones de enrutamiento."""
        return list(self._decision_history)
