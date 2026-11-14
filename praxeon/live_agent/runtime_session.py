"""Tareas de `praxeon live` y `praxeon-dash --live` sobre el runtime y el bucle canónico (auditoría A-02).

La terminal ya no tiene un bucle de agente propio: cada tarea es una misión de un ``RuntimeApplicationService`` en el
propio proceso, con la misma política, sandbox y recibos que la app y la web. ``TerminalMission`` lanza la misión,
recibe sus eventos en el hilo principal (el que pinta la terminal), pide en la terminal las confirmaciones humanas y
devuelve el resultado cuando el worker de la misión termina.
"""

from __future__ import annotations

import os
import queue
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from praxeon.domain.events import EventType, RuntimeEvent

# Nombres del supervisor en la CLI -> proveedor de decisión del runtime
SUPERVISOR_ALIASES = {"jev": "typesafe", "typesafe": "typesafe", "laya": "laya", "laya-system1": "laya", "laya-v1": "laya"}
# Proveedores del agente que piden el planificador sintético
SIMULATED_PROVIDERS = ("simulated", "simulator", "sim", "mock")
FINISH_TOOLS = ("finish", "complete_task", "done", "complete", "task_completed")
# Una persona en la terminal puede tardar en decidir: la misión espera su respuesta hasta una hora
APPROVAL_TIMEOUT_SECONDS = 3600

EventHandler = Callable[[RuntimeEvent], None]
Approver = Callable[[RuntimeEvent], bool]


@dataclass
class LiveTaskResult:
    """Resultado de una tarea de terminal."""

    session_id: str
    finished: bool
    final_answer: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    fatal_error: Optional[str] = None
    llm_error: Optional[str] = None   # el modelo del agente falló repetidamente (cortacircuitos técnico)
    degraded: bool = False
    agent_finished: bool = False


def _deny_all(event: RuntimeEvent) -> bool:
    return False


class TerminalMission:
    """Ejecuta tareas de la terminal como misiones del runtime en proceso (una por llamada a ``run``)."""

    def __init__(
        self,
        service: Any = None,
        *,
        db_dir: Optional[str] = None,
        on_event: Optional[EventHandler] = None,
        approver: Optional[Approver] = None,
        poll_seconds: float = 0.1,
    ):
        self._service = service
        self._db_dir = db_dir
        self.on_event = on_event
        # Sin forma de preguntar (p. ej. entrada no interactiva) nada que exija confirmación se aprueba solo
        self.approver = approver or _deny_all
        self.poll_seconds = poll_seconds

    @property
    def service(self) -> Any:
        if self._service is None:
            from praxeon.server.dependencies import RuntimeApplicationService
            self._service = RuntimeApplicationService(db_dir=self._db_dir)
        return self._service

    def run(
        self,
        goal: str,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_steps: int = 15,
        supervisor: str = "laya",
        llm_failure_policy: str = "synthetic_fallback",
        chat_history: Optional[List[Dict[str, str]]] = None,
        workspace_root: Optional[str] = None,
        execution_mode: Optional[str] = None,
    ) -> LiveTaskResult:
        """Lanza la misión y atiende sus eventos hasta que el worker termina."""
        svc = self.service
        sid = f"live-{uuid.uuid4().hex[:8]}"
        events: "queue.Queue[RuntimeEvent]" = queue.Queue()
        unsubscribe = svc.event_bus.subscribe(events.put, session_id=sid)
        result = LiveTaskResult(session_id=sid, finished=False, final_answer="")
        try:
            provider_name = (provider or "auto").lower()
            svc.start_mission(
                goal=goal,
                session_id=sid,
                agent_name="LiveAgent",
                execution_mode=execution_mode,
                workspace_root=workspace_root or os.getcwd(),
                llm_provider="simulator" if provider_name in SIMULATED_PROVIDERS else provider_name,
                llm_model=model,
                api_key=api_key,
                base_url=base_url,
                supervisor=SUPERVISOR_ALIASES.get((supervisor or "laya").lower(), supervisor),
                max_steps=max_steps,
                step_delay_ms=0,
                llm_failure_policy=llm_failure_policy,
                chat_history=chat_history,
                metadata={"interface": "terminal", "approval_timeout_seconds": APPROVAL_TIMEOUT_SECONDS},
            )
            worker = self._worker(sid)
            while True:
                try:
                    event = events.get(timeout=self.poll_seconds)
                except queue.Empty:
                    if worker is None or not worker.is_alive():
                        break
                    continue
                self._handle(svc, event, result)
            while not events.empty():   # lo que el worker emitió justo antes de terminar
                self._handle(svc, events.get_nowait(), result)
        finally:
            unsubscribe()

        meta = svc._sessions_meta.get(sid) or {}
        answer = meta.get("final_answer") or result.final_answer
        result.final_answer = str(answer or "")
        # Terminada = el agente concluyó con un finish aceptado (no un corte del supervisor ni el límite de pasos)
        result.finished = result.fatal_error is None and result.agent_finished
        return result

    @staticmethod
    def _worker(sid: str) -> Optional[threading.Thread]:
        name = f"mission-worker-{sid}"
        return next((t for t in threading.enumerate() if t.name == name), None)

    def _handle(self, svc: Any, event: RuntimeEvent, result: LiveTaskResult) -> None:
        payload = event.payload or {}
        if event.type == EventType.ACTION_PROPOSED:
            result.steps.append({
                "action_id": payload.get("action_id"),
                "tool": payload.get("tool"),
                "arguments": payload.get("arguments") or {},
                "thought": payload.get("thought_rationale") or "",
                "synthetic_fallback": bool(payload.get("synthetic_fallback")),
                "model_source": payload.get("model_source"),
            })
        elif event.type == EventType.POLICY_DECIDED and result.steps:
            result.steps[-1]["verdict"] = payload.get("status")
        elif event.type == EventType.OBSERVATION_RECORDED and result.steps:
            result.steps[-1]["observation"] = payload.get("output", "")
            if result.steps[-1].get("tool") in FINISH_TOOLS:
                result.agent_finished = True   # el finish del agente se aceptó y se ejecutó
        elif event.type == EventType.INTERVENTION_APPLIED:
            if payload.get("fatal"):
                result.fatal_error = str(payload.get("error") or payload.get("message") or "Fallo fatal de la misión")
            if payload.get("degraded_mode"):
                result.degraded = True
            if payload.get("intervention") == "TECHNICAL_CIRCUIT_BREAKER":
                result.llm_error = str(payload.get("message") or "El modelo del agente falló repetidamente")
        elif event.type == EventType.SESSION_COMPLETED and payload.get("summary"):
            result.final_answer = str(payload["summary"])

        if self.on_event is not None:
            self.on_event(event)

        if event.type == EventType.APPROVAL_REQUESTED and event.decision_id:
            approved = bool(self.approver(event))
            svc.confirm_decision(
                decision_id=event.decision_id,
                approved=approved,
                reason="Aprobada por el operador en la terminal" if approved else "Rechazada por el operador en la terminal",
                operator_id="terminal_operator",
                role="operator",
            )
