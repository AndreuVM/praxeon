"""Sistema de observabilidad, eventos estructurados y telemetría para JEV Reasoning Navigator.

Implementa los requisitos de las Secciones 21 y 30 de la Auditoría Técnica:
- Eventos estructurados sin almacenamiento indiscriminado de contenido sensible
- Modelos inmutables: DecisionEvent, ToolExecutionEvent, ObservationEvent, InterventionEvent
- Bus de eventos (EventBus) desacoplado para integración con sinks de logs o métricas
- Sink JSONL para persistencia local de auditoría
"""

from datetime import datetime, timezone
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("praxeon.runtime.telemetry")


class TelemetryEvent(BaseModel):
    """Clase base para todos los eventos de auditoría y telemetría estructurados."""
    model_config = ConfigDict(frozen=True)

    event_type: str
    timestamp: float = Field(default_factory=time.time)
    iso_timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str


class DecisionEvent(TelemetryEvent):
    """Evento emitido cada vez que PolicyEngine evalúa una acción candidata."""
    event_type: str = "policy_decision"
    decision_id: str
    action_id: str
    state_hash: str
    action_hash: str
    provider: str
    model: Optional[str] = None
    decision: str
    confidence: float = 0.0
    risk: str
    latency_ms: float
    reason_codes: List[str] = Field(default_factory=list)
    shadow_mode: bool = False


class ToolExecutionEvent(TelemetryEvent):
    """Evento emitido tras la ejecución física de una herramienta."""
    event_type: str = "tool_execution"
    action_id: str
    tool_name: str
    arguments_hash: str
    success: bool
    execution_time_ms: float
    is_error: bool = False


class ObservationEvent(TelemetryEvent):
    """Evento emitido tras capturar e indexar una observación del entorno."""
    event_type: str = "observation_captured"
    observation_id: str
    tool_name: Optional[str] = None
    content_hash: str
    size_bytes: int


class InterventionEvent(TelemetryEvent):
    """Evento emitido cuando el supervisor genera una directiva correctiva discursiva."""
    event_type: str = "supervisor_intervention"
    level: int
    target_step_id: Optional[str] = None
    forbidden_actions: List[str] = Field(default_factory=list)
    suggested_action: Optional[str] = None


class EfficiencyStepEvent(TelemetryEvent):
    """Evento emitido para registrar métricas de telemetría de eficiencia de un paso normativo."""
    event_type: str = "step_efficiency"
    step_index: int
    action_id: str
    tool_name: str
    decision: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_total: int = 0
    llm_calls: int = 1
    llm_latency_ms: float = 0.0
    praxeon_overhead_ms: float = 0.0
    execution_time_ms: float = 0.0
    total_step_latency_ms: float = 0.0
    physical_execution_attempted: bool = False
    physical_execution_allowed: bool = False
    physical_execution_success: Optional[bool] = None
    is_error: bool = False
    cost_usd: float = 0.0



class JsonlTelemetrySink:
    """Persiste eventos estructurados en un archivo en formato JSON Lines."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)

    def write_event(self, event: TelemetryEvent) -> None:
        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")
        except Exception as e:
            logger.error(f"Error escribiendo evento de telemetría a {self.file_path}: {e}")


class EventBus:
    """Bus in-process para suscripción y publicación reactiva de eventos."""

    def __init__(self):
        self._subscribers: List[Callable[[TelemetryEvent], None]] = []

    def subscribe(self, callback: Callable[[TelemetryEvent], None]) -> None:
        """Registra un nuevo receptor de eventos."""
        self._subscribers.append(callback)

    def publish(self, event: TelemetryEvent) -> None:
        """Notifica a todos los receptores registrados."""
        for callback in self._subscribers:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Error en suscriptor de EventBus ({callback}): {e}")

    def emit(
        self,
        session_id: str,
        event_type: Any,
        node_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> TelemetryEvent:
        """Crea y publica un evento básico de telemetría para compatibilidad con la interfaz emit()."""
        ev_type_str = getattr(event_type, "value", str(event_type))
        event = TelemetryEvent(
            event_type=ev_type_str,
            session_id=session_id,
        )
        self.publish(event)
        return event


# Bus global singleton por defecto
global_event_bus = EventBus()
