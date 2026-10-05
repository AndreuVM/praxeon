"""Paquete Adaptive Agent Runtime de PRAXEON (Fase 8).

Provee la integración holística y adaptativa de agentes en tiempo de ejecución:
- AdaptiveAgentRuntime: Motor central unificado de extremo a extremo.
- StepLevelAdaptiveDispatcher: Despacho y autorización atómica bajo 'Semántica != Autoridad'.
- TrajectoryController: Supervisión y gobierno dinámico de trayectorias (Continue, Prune, Recover, Escalate).
- Modelos de Dominio: TrajectoryDirective, StepDispatchSpec, TrajectoryStepRecord,
  AdaptiveSessionState, AdaptiveExecutionSummary.
"""

from praxeon.runtime.adaptive.models import (
    AdaptiveExecutionSummary,
    AdaptiveSessionState,
    StepDispatchSpec,
    TrajectoryDirective,
    TrajectoryStepRecord,
)
from praxeon.runtime.adaptive.runtime import AdaptiveAgentRuntime
from praxeon.runtime.adaptive.step_dispatcher import StepLevelAdaptiveDispatcher
from praxeon.runtime.adaptive.trajectory_controller import TrajectoryController

__all__ = [
    "AdaptiveAgentRuntime",
    "AdaptiveExecutionSummary",
    "AdaptiveSessionState",
    "StepDispatchSpec",
    "StepLevelAdaptiveDispatcher",
    "TrajectoryController",
    "TrajectoryDirective",
    "TrajectoryStepRecord",
]
