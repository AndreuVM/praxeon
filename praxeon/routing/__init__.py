"""Módulo de Enrutamiento Inteligente y Despacho de Agentes (Fase 7).

Provee la infraestructura de enrutamiento dinámico hacia agentes especializados:
- TaskRequirement & TaskComplexity: Especificación de requerimientos de tareas.
- RoutingStrategyType & RoutingDecision: Modos de selección y decisiones formales.
- AgentRouter: Enrutador central y despachador sobre AgentMessageBus.
"""

from praxeon.routing.classifier import TaskClassifier
from praxeon.routing.escalation import (
    DynamicEscalationEngine,
    EscalationContext,
    EscalationResult,
    EscalationTriggerType,
)
from praxeon.routing.models import (
    RoutingDecision,
    RoutingStrategyType,
    TaskComplexity,
    TaskRequirement,
)
from praxeon.routing.router import AgentRouter, IneligibleAgentRoutingError
from praxeon.routing.strategies import (
    AdaptiveRoutingStrategy,
    CostAwareRoutingStrategy,
    SemanticRoutingStrategy,
)

__all__ = [
    "AdaptiveRoutingStrategy",
    "AgentRouter",
    "CostAwareRoutingStrategy",
    "DynamicEscalationEngine",
    "EscalationContext",
    "EscalationResult",
    "EscalationTriggerType",
    "IneligibleAgentRoutingError",
    "RoutingDecision",
    "RoutingStrategyType",
    "SemanticRoutingStrategy",
    "TaskClassifier",
    "TaskComplexity",
    "TaskRequirement",
]
