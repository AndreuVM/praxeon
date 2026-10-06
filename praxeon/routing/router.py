"""Módulo Central de Enrutamiento Inteligente de Agentes (AgentRouter) (F7-01).

Implementa la asignación determinista y semántica de tareas hacia agentes especializados:
- AgentRouter:
    * Filtrado estricto de elegibilidad según capacidades, contención de privilegios y tolerancia de riesgo.
    * Estrategias de enrutamiento conmutables (MANUAL, STATIC, RULE_BASED, SEMANTIC, COST_AWARE).
    * Despacho coordinado mediante AgentMessageBus hacia los buzones dedicados de los agentes.
    * Registro histórico auditable de decisiones de ruteo con scores de confianza.
"""

from datetime import datetime, timezone
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

logger = logging.getLogger("praxeon.routing.router")

from praxeon.agents.bus import AgentMessageBus
from praxeon.agents.definition import AgentDefinition, AgentStatus
from praxeon.agents.protocol import AgentMessage, MessagePriority, MessageType
from praxeon.agents.registry import AgentRegistry
from praxeon.config import PraxeonConfig
from praxeon.domain.assessment import RiskLevel
from praxeon.domain.events import EventType
from praxeon.domain.models import ActionCandidate, ToolCall
from praxeon.providers.base import BaseReasoningProvider
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


class IneligibleAgentRoutingError(ValueError):
    """Excepción formal cuando un agente asignado manualmente no cumple con los criterios de elegibilidad de la tarea."""

    def __init__(
        self,
        agent_id: str,
        task_id: str,
        reason: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.agent_id = agent_id
        self.task_id = task_id
        self.reason = reason
        self.details = details or {}
        super().__init__(f"Agente manual '{agent_id}' no es elegible para la tarea '{task_id}': {reason}")


class AgentRouter:
    """Enrutador central para despacho de misiones hacia agentes idóneos."""

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        default_strategy: RoutingStrategyType = RoutingStrategyType.RULE_BASED,
        allow_experimental_branching: bool = False,
        enforce_deterministic_baseline: bool = False,
        config: Optional[PraxeonConfig] = None,
        message_bus: Optional[AgentMessageBus] = None,
        llm_provider: Optional[BaseReasoningProvider] = None,
        event_bus: Optional[Any] = None,
    ):
        self.registry = registry or AgentRegistry()
        self.default_strategy = default_strategy
        self.config = config
        self.allow_experimental_branching = allow_experimental_branching
        self.enforce_deterministic_baseline = enforce_deterministic_baseline
        self.message_bus = message_bus
        self.llm_provider = llm_provider
        self.event_bus = event_bus
        if config is not None:
            if config.adaptive.enabled:
                self.allow_experimental_branching = True
            if config.adaptive.fallback_to_deterministic:
                self.enforce_deterministic_baseline = True

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
            decision_id=f"rd_static_{uuid.uuid4().hex[:12]}",
            task_id=task.task_id,
            selected_agent_id=selected.agent_id,
            confidence=0.8,
            strategy_used=RoutingStrategyType.STATIC,
            alternative_agent_ids=alts[:3],
            rationale=f"Asignación estática determinista hacia '{selected.name}' ({selected.role}).",
            matched_capabilities=[c for c in task.required_capabilities if c in selected.capabilities],
            metadata={
                "experimental": False,
                "is_experimental": False,
                "baseline_mode": "deterministic",
            },
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
        if req_caps_count > 0:
            cap_ratio = matched_caps_count / req_caps_count
            base_conf = 0.7 * cap_ratio
        else:
            # Sin capabilities declaradas, la confianza depende de si hubo coincidencia en rol/skills
            base_conf = 0.65 if top_score > 1.0 else 0.50

        affinity_bonus = min(0.28, max(0.0, (top_score - 1.0) * 0.05))
        norm_confidence = min(0.99, max(0.5, round(base_conf + affinity_bonus, 3)))
        alternatives = [a.agent_id for _, a in scored_agents[1:4]]

        return RoutingDecision(
            decision_id=f"rd_rule_{uuid.uuid4().hex[:12]}",
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
            metadata={
                "experimental": False,
                "is_experimental": False,
                "baseline_mode": "deterministic",
            },
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
                raise IneligibleAgentRoutingError(
                    agent_id=target_id,
                    task_id=task.task_id,
                    reason=f"Agente manual '{target_id}' no encontrado en el registro.",
                )
            if agent.status != AgentStatus.ACTIVE:
                raise IneligibleAgentRoutingError(
                    agent_id=target_id,
                    task_id=task.task_id,
                    reason=f"Agente manual '{target_id}' no se encuentra en estado ACTIVE (estado actual: {agent.status.value}).",
                    details={"status": agent.status.value},
                )

            # Filtro estricto de elegibilidad: selected ∈ eligible
            eligible = self.filter_eligible_agents(task)
            eligible_ids = {a.agent_id for a in eligible}
            if agent.agent_id not in eligible_ids:
                reasons = []
                details: Dict[str, Any] = {}
                prohibited = [t for t in task.required_tools if not agent.is_tool_allowed(t)]
                if prohibited:
                    reasons.append(f"Herramientas prohibidas requeridas por la tarea: {prohibited}")
                    details["prohibited_tools"] = prohibited

                risk_weights = {
                    RiskLevel.LOW: 1,
                    RiskLevel.MEDIUM: 2,
                    RiskLevel.HIGH: 3,
                    RiskLevel.CRITICAL: 4,
                }
                task_risk_val = risk_weights.get(task.inferred_risk, 1)
                ag_risk_val = risk_weights.get(agent.risk_profile.max_risk_level, 2)
                if task_risk_val > ag_risk_val and not agent.risk_profile.require_human_confirmation:
                    reasons.append(
                        f"Riesgo de tarea ({task.inferred_risk.value}) supera tolerancia ({agent.risk_profile.max_risk_level.value})"
                    )
                    details["task_risk"] = task.inferred_risk.value
                    details["agent_max_risk"] = agent.risk_profile.max_risk_level.value

                missing_caps = [c for c in task.required_capabilities if c not in agent.capabilities]
                if missing_caps:
                    reasons.append(f"Carece de capabilities obligatorias: {missing_caps}")
                    details["missing_capabilities"] = missing_caps

                explanation = "; ".join(reasons) if reasons else "El agente no cumple con los filtros de elegibilidad de la tarea."
                raise IneligibleAgentRoutingError(
                    agent_id=agent.agent_id,
                    task_id=task.task_id,
                    reason=explanation,
                    details=details,
                )

            decision = RoutingDecision(
                decision_id=f"rd_man_{uuid.uuid4().hex[:12]}",
                task_id=task.task_id,
                selected_agent_id=agent.agent_id,
                confidence=1.0,
                strategy_used=RoutingStrategyType.MANUAL,
                rationale=f"Asignación manual forzada y validada por el operador hacia '{agent.name}'.",
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

        # Gobernanza de branching adaptativo experimental vs baseline determinista
        if active_strat == RoutingStrategyType.ADAPTIVE:
            is_branching_allowed = (
                self.allow_experimental_branching
                or (self.config is not None and self.config.adaptive.enabled)
                or bool(task.metadata.get("allow_experimental_branching", False))
            )
            if self.enforce_deterministic_baseline and not is_branching_allowed:
                raw_decision = self._route_rule_based(task, eligible)
                new_meta = dict(raw_decision.metadata or {})
                new_meta.update({
                    "experimental_branching_blocked": True,
                    "fallback_to_deterministic": True,
                    "experimental": False,
                    "is_experimental": False,
                    "baseline_mode": "deterministic",
                })
                decision = raw_decision.model_copy(
                    update={
                        "rationale": (
                            f"[BASELINE DETERMINISTA] Fallback a reglas deterministas porque el branching adaptativo "
                            f"es experimental y no está habilitado: {raw_decision.rationale}"
                        ),
                        "metadata": new_meta,
                    }
                )
                self._decision_history.append(decision)
                return decision

        handler = self._strategy_handlers.get(active_strat)
        if not handler:
            # Fallback a rule-based
            handler = self._route_rule_based

        decision = handler(task, eligible)

        # DEUDA-ROUT-01: Fallback inteligente a LLM si la confianza heurística es baja (< 0.65)
        if decision.confidence < 0.65 and self.llm_provider is not None:
            decision = self._route_llm_fallback(task, eligible, decision)

        new_meta = dict(decision.metadata or {})
        if active_strat == RoutingStrategyType.ADAPTIVE:
            new_meta.setdefault("experimental", True)
            new_meta.setdefault("is_experimental", True)
            new_meta.setdefault("baseline_mode", "adaptive_experimental")
        else:
            new_meta.setdefault("experimental", False)
            new_meta.setdefault("is_experimental", False)
            new_meta.setdefault("baseline_mode", "deterministic")

        if new_meta != decision.metadata:
            decision = decision.model_copy(update={"metadata": new_meta})

        self._decision_history.append(decision)
        return decision

    def _route_llm_fallback(
        self,
        task: TaskRequirement,
        eligible: List[AgentDefinition],
        previous_decision: RoutingDecision,
    ) -> RoutingDecision:
        """Invoca al LLM / razonador semántico para resolver ambigüedad cuando la heurística tiene baja confianza."""
        if not self.llm_provider or not eligible:
            return previous_decision

        try:
            candidates: List[ActionCandidate] = []
            for ag in eligible:
                candidates.append(
                    ActionCandidate(
                        id=f"act_assign_{ag.agent_id}",
                        description=f"Asignar tarea '{task.task_id}' al agente {ag.name} ({ag.role})",
                        tool_call=ToolCall(
                            tool_name="assign_agent",
                            arguments={
                                "agent_id": ag.agent_id,
                                "name": ag.name,
                                "role": ag.role,
                                "capabilities": ag.capabilities,
                                "skills": ag.skills,
                            },
                        ),
                    )
                )

            state_desc = {
                "task_prompt": task.prompt,
                "complexity": task.complexity.value,
                "required_capabilities": task.required_capabilities,
                "inferred_risk": task.inferred_risk.value,
            }

            assessments = self.llm_provider.evaluate(state=state_desc, actions=candidates)
            if assessments and len(assessments) == len(candidates):
                best_idx = 0
                best_conf = -1.0
                for idx, assess in enumerate(assessments):
                    if assess.confidence > best_conf:
                        best_conf = assess.confidence
                        best_idx = idx

                selected_ag = eligible[best_idx]
                best_assess = assessments[best_idx]
                rationale_snippet = (
                    getattr(best_assess, "rationale", None)
                    or best_assess.metadata.get("rationale")
                    or best_assess.metadata.get("explanation")
                    or (best_assess.reason_codes[0] if best_assess.reason_codes else None)
                    or f"Agente {selected_ag.name} ({selected_ag.role}) seleccionado."
                )
                provider_name = getattr(self.llm_provider, "name", "llm")
                rationale_text = (
                    f"[LLM FALLBACK - {provider_name}] Selección semántica por ambigüedad heurística "
                    f"(confianza previa: {previous_decision.confidence}): "
                    f"{rationale_snippet}"
                )
                boosted_conf = max(0.80, min(0.99, best_conf if best_conf > 0 else 0.85))

                fallback_meta = dict(previous_decision.metadata or {})
                fallback_meta.update({
                    "llm_fallback_triggered": True,
                    "previous_confidence": previous_decision.confidence,
                    "previous_agent_id": previous_decision.selected_agent_id,
                    "provider": provider_name,
                })

                new_decision = RoutingDecision(
                    decision_id=f"rd_llm_{uuid.uuid4().hex[:12]}",
                    task_id=task.task_id,
                    selected_agent_id=selected_ag.agent_id,
                    confidence=boosted_conf,
                    strategy_used=previous_decision.strategy_used,
                    alternative_agent_ids=[a.agent_id for a in eligible if a.agent_id != selected_ag.agent_id][:3],
                    rationale=rationale_text,
                    matched_capabilities=[c for c in task.required_capabilities if c in selected_ag.capabilities],
                    metadata=fallback_meta,
                )

                if self.event_bus and hasattr(self.event_bus, "emit"):
                    try:
                        self.event_bus.emit(
                            session_id=task.metadata.get("session_id", "routing_plane"),
                            event_type=EventType.INTERVENTION_APPLIED,
                            payload={
                                "intervention_type": "routing_llm_fallback",
                                "task_id": task.task_id,
                                "selected_agent_id": selected_ag.agent_id,
                                "previous_agent_id": previous_decision.selected_agent_id,
                                "previous_confidence": previous_decision.confidence,
                                "new_confidence": boosted_conf,
                                "rationale": rationale_text,
                            },
                        )
                    except Exception:
                        pass

                return new_decision

        except Exception as exc:
            logger.warning(f"Error en fallback LLM de enrutador: {exc}")

        return previous_decision

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
            message_id=f"msg_route_{task.task_id}_{uuid.uuid4().hex[:8]}",
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
