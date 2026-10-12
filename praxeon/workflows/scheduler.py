"""Scheduler y Worker en Segundo Plano para Timeouts y Reintentos de Workflows (DEUDA-WF-03).

Proporciona supervisión continua e independiente del ciclo de vida de ejecuciones de workflow:
- Verificación de timeouts por nodo y timeouts globales del flujo (workflow.timeout_seconds).
- Transición automática de nodos en RETRYING a READY cuando se cumple el tiempo de backoff (now >= retry_after).
- Ejecución desatendida de pasos concurrentes listos sin necesidad de polling manual por parte del cliente.
- Persistencia periódica de checkpoints en SqlitePersistenceStore tras cada ciclo de evaluación.
"""

from datetime import datetime, timezone
import logging
import threading
from typing import Any, Dict, Optional

from praxeon.domain.events import EventType
from praxeon.workflows.engine import WorkflowEngine
from praxeon.workflows.models import NodeStatus, WorkflowStatus

logger = logging.getLogger("praxeon.workflows.scheduler")


class WorkflowScheduler:
    """Worker en segundo plano para supervisión y avance de workflows activos."""

    def __init__(
        self,
        persistence_store: Optional[Any] = None,
        interval_seconds: float = 0.5,
        event_bus: Optional[Any] = None,
    ):
        self.persistence_store = persistence_store
        self.event_bus = event_bus
        self._waiting_nodes: Dict[str, set] = {}
        self.interval_seconds = max(0.05, interval_seconds)
        self._engines: Dict[str, WorkflowEngine] = {}
        self._persisted_signatures: Dict[str, tuple] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def register_engine(self, engine: WorkflowEngine) -> None:
        """Registra un WorkflowEngine para supervisión periódica en segundo plano."""
        with self._lock:
            key = engine.context.execution_id
            self._engines[key] = engine
            # También persistir estado inicial si hay almacén
            if self.persistence_store and hasattr(self.persistence_store, "save_workflow_execution"):
                try:
                    self.persistence_store.save_workflow_execution(engine.get_execution())
                except Exception as exc:
                    logger.warning(f"Error al persistir ejecución inicial en scheduler: {exc}")

    def unregister_engine(self, identifier: str) -> Optional[WorkflowEngine]:
        """Elimina un motor de la supervisión activa (por execution_id o workflow_id)."""
        with self._lock:
            # Buscar por execution_id
            if identifier in self._engines:
                return self._engines.pop(identifier)
            # Buscar por workflow_id
            for eid, eng in list(self._engines.items()):
                if eng.workflow.workflow_id == identifier:
                    return self._engines.pop(eid)
            return None

    def get_active_engines(self) -> Dict[str, WorkflowEngine]:
        """Retorna una copia del diccionario de motores supervisados."""
        with self._lock:
            return dict(self._engines)

    def is_running(self) -> bool:
        """Indica si el worker en segundo plano está activo."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Inicia el hilo supervisor en segundo plano."""
        with self._lock:
            if self.is_running():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="praxeon-workflow-scheduler",
                daemon=True,
            )
            self._thread.start()
            logger.info("WorkflowScheduler iniciado en segundo plano.")

    def stop(self, timeout: float = 2.0) -> None:
        """Detiene el hilo supervisor de forma limpia y sincronizada."""
        with self._lock:
            self._stop_event.set()
            thread = self._thread

        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        logger.info("WorkflowScheduler detenido.")

    def poll_once(self, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """Ejecuta una ronda de inspección, reactivación de reintentos y control de timeouts.

        Retorna un informe con los cambios producidos durante la ronda.
        """
        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        report: Dict[str, Any] = {
            "evaluated_engines": 0,
            "timed_out_workflows": [],
            "retried_nodes": [],
            "stepped_nodes": [],
            "completed_workflows": [],
        }

        with self._lock:
            engines_snapshot = list(self._engines.items())

        for exec_id, engine in engines_snapshot:
            report["evaluated_engines"] += 1
            ctx = engine.context

            # 1. Si el workflow ya concluyó en un estado terminal, persistir y desregistrar
            if ctx.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                self._persist_execution_safe(engine)
                report["completed_workflows"].append(exec_id)
                with self._lock:
                    self._engines.pop(exec_id, None)
                continue

            # 2. Timeout global del workflow (lo aplica el propio motor, bajo su bloqueo)
            if engine.check_global_timeout(current_time=now):
                self._persist_execution_safe(engine)
                report["timed_out_workflows"].append(exec_id)
                with self._lock:
                    self._engines.pop(exec_id, None)
                continue

            # 3. Comprobar reintentos y timeouts a nivel de nodo
            eval_res = engine.check_scheduled_retries_and_timeouts(current_time=now)
            ready_from_retry = eval_res.get("ready_from_retry", [])
            if ready_from_retry:
                report["retried_nodes"].extend(ready_from_retry)

            # 4. Avanzar nodos que estén listos de forma concurrente
            if ctx.status == WorkflowStatus.RUNNING:
                try:
                    executed_nodes = engine.step_concurrent(current_time=now)
                    if executed_nodes:
                        report["stepped_nodes"].extend(executed_nodes)
                except Exception as exc:
                    logger.error(f"Error al avanzar nodos concurrentes en workflow {exec_id}: {exc}")

            # 5. Avisar de aprobaciones nuevas/resueltas y persistir estado tras el ciclo
            self._publish_approval_transitions(exec_id, engine)
            self._persist_execution_safe(engine)

            # Si concluyó tras el step, registrarlo
            if ctx.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                report["completed_workflows"].append(exec_id)
                with self._lock:
                    self._engines.pop(exec_id, None)

        return report

    def sync_engine(self, engine: WorkflowEngine) -> None:
        """Publica aprobaciones y persiste el estado de un motor que avanza fuera del scheduler (misiones con workflow)."""
        self._publish_approval_transitions(engine.context.execution_id, engine)
        self._persist_execution_safe(engine)

    def _publish_approval_transitions(self, exec_id: str, engine: WorkflowEngine) -> None:
        """Emite eventos cuando un nodo entra o sale de WAITING_APPROVAL (alimenta notificaciones y la UI)."""
        if self.event_bus is None:
            return
        with engine._lock:
            waiting = {
                nid for nid, st in engine.context.node_states.items() if st == NodeStatus.WAITING_APPROVAL
            }
            outputs = {nid: dict(engine.context.node_outputs.get(nid, {})) for nid in waiting}
        previous = self._waiting_nodes.get(exec_id, set())
        try:
            for nid in sorted(waiting - previous):
                node = engine.workflow.nodes[nid]
                out = outputs.get(nid, {})
                self.event_bus.emit(
                    session_id=exec_id,
                    event_type=EventType.APPROVAL_REQUESTED,
                    node_id=nid,
                    payload={
                        "source": "workflow",
                        "workflow_id": engine.workflow.workflow_id,
                        "execution_id": exec_id,
                        "node_name": node.name,
                        "tool": node.tool_name,
                        "prompt": out.get("prompt"),
                        "reason_codes": out.get("reason_codes"),
                    },
                )
            for nid in sorted(previous - waiting):
                self.event_bus.emit(
                    session_id=exec_id,
                    event_type=EventType.APPROVAL_COMPLETED,
                    node_id=nid,
                    payload={"source": "workflow", "execution_id": exec_id},
                )
        except Exception as exc:
            logger.warning(f"Error publicando eventos de aprobación: {exc}")
        if engine.context.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
            self._waiting_nodes.pop(exec_id, None)
        else:
            self._waiting_nodes[exec_id] = waiting

    def _persist_execution_safe(self, engine: WorkflowEngine) -> None:
        """Persiste de forma segura el estado de ejecución si hay almacén configurado."""
        if self.persistence_store and hasattr(self.persistence_store, "save_workflow_execution"):
            ctx = engine.context
            # Solo se persiste si el estado cambió desde el último guardado (evita reescribir cada ciclo)
            signature = (
                ctx.status,
                tuple(sorted((k, v.value) for k, v in ctx.node_states.items())),
                len(ctx.execution_history),
                ctx.finished_at,
            )
            if self._persisted_signatures.get(ctx.execution_id) == signature:
                return
            try:
                self.persistence_store.save_workflow_execution(engine.get_execution())
                self._persisted_signatures[ctx.execution_id] = signature
            except Exception as exc:
                logger.warning(f"Error al persistir ejecución en scheduler: {exc}")
            if ctx.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                self._persisted_signatures.pop(ctx.execution_id, None)

    def _run_loop(self) -> None:
        """Bucle continuo ejecutado en el hilo en segundo plano."""
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                logger.error(f"Error inesperado en ciclo de WorkflowScheduler: {exc}", exc_info=True)
            self._stop_event.wait(timeout=self.interval_seconds)
