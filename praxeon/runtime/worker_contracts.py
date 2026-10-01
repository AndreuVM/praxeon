"""Contratos y Abstracciones Arquitecturales para Desacoplamiento de LiveMissionWorker (v1.0.0).

Define los contratos formales para modularizar el worker monolítico en tres componentes
de responsabilidad única:
1. AgentAdapter: Interacción con el proveedor LLM y parsing estricto ReAct.
2. TrajectoryController: Supervisión de bucles semánticos, backtracking, checkpoints y circuit breakers duales.
3. MissionRunner: Orquestación del ciclo de vida de la sesión (pausa, reanudación, retardo y terminación).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple


@dataclass(frozen=True)
class ParsedStep:
    """Paso ReAct parseado emitido por un agente o adaptador."""
    tool_name: str
    tool_args: Dict[str, Any] = field(default_factory=dict)
    thought_rationale: str = ""
    raw_output: str = ""
    is_finish: bool = False


@dataclass
class CircuitBreakerStatus:
    """Estado unificado del Circuit Breaker Dual (Técnico y Semántico)."""
    technical_failures: int = 0
    technical_circuit_open: bool = False
    semantic_fixations: int = 0
    semantic_circuit_open: bool = False
    recent_action_signatures: List[str] = field(default_factory=list)


class AgentAdapterProtocol(Protocol):
    """Protocolo para adaptadores de agentes LLM que generan y parsean pasos ReAct."""

    @abstractmethod
    def generate_step(
        self,
        conversation: List[Dict[str, str]],
        system_prompt: str,
        goal: str,
        step_idx: int,
    ) -> ParsedStep:
        """Invoca al LLM subyacente y retorna un paso estructurado validado."""
        ...


class TrajectoryControllerProtocol(Protocol):
    """Protocolo para supervisión cognitiva, control de bucles y gestión de ramas."""

    @abstractmethod
    def record_action_attempt(self, tool: str, args: Dict[str, Any]) -> bool:
        """Registra una propuesta de acción y retorna True si se detectó bucle semántico."""
        ...

    @abstractmethod
    def record_technical_failure(self, error: Exception) -> bool:
        """Registra un fallo técnico de red/proveedor y retorna True si se abrió el circuito técnico."""
        ...

    @abstractmethod
    def record_success(self) -> None:
        """Notifica éxito operacional y resetea contadores transitorios."""
        ...

    @abstractmethod
    def get_circuit_status(self) -> CircuitBreakerStatus:
        """Retorna el estado actual de los circuitos de protección."""
        ...


class MissionRunnerProtocol(Protocol):
    """Protocolo para la gestión del ciclo de vida y despacho asíncrono de misiones."""

    @abstractmethod
    def start(self, session_id: str, goal: str, **kwargs: Any) -> None:
        """Inicia el bucle de ejecución supervisado."""
        ...

    @abstractmethod
    def pause(self, session_id: str) -> bool:
        """Pausa temporalmente la misión."""
        ...

    @abstractmethod
    def resume(self, session_id: str) -> bool:
        """Reanuda la misión pausada."""
        ...

    @abstractmethod
    def stop(self, session_id: str) -> bool:
        """Detiene definitivamente la misión."""
        ...
