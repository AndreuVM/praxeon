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
import time
from typing import Any, Dict, List, Optional

from praxeon.workflows.engine import WorkflowEngine
from praxeon.workflows.models import NodeStatus, WorkflowExecution, WorkflowStatus

logger = logging.getLogger("praxeon.workflows.scheduler")


class WorkflowScheduler:
    """Worker en segundo plano para supervisión y avance de workflows activos."""

    def __init__(
        self,
        persistence_store: Optional[Any] = None,
        interval_seconds: float = 0.5,
    ):
        self.persistence_store = persistence_store
        self.interval_seconds = max(0.05, interval_seconds)
        self._engines: Dict[str, WorkflowEngine] = {}
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

            # 2. Comprobar timeout global del workflow si está definido
            timeout_sec = getattr(engine.workflow, "timeout_seconds", None)
            if timeout_sec and ctx.started_at:
                elapsed = (now - ctx.started_at).total_seconds()
                if elapsed >= timeout_sec:
                    ctx.status = WorkflowStatus.FAILED
                    ctx.error_message = f"Timeout global de workflow ({timeout_sec}s) excedido."
                    ctx.finished_at = now
                    for nid, st in ctx.node_states.items():
                        if st in (NodeStatus.RUNNING, NodeStatus.WAITING_RESULT, NodeStatus.READY, NodeStatus.PENDING):
                            ctx.node_states[nid] = NodeStatus.TIMEOUT
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

            # 5. Persistir estado tras el ciclo
            self._persist_execution_safe(engine)

            # Si concluyó tras el step, registrarlo
            if ctx.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                report["completed_workflows"].append(exec_id)
                with self._lock:
                    self._engines.pop(exec_id, None)

        return report

    def _persist_execution_safe(self, engine: WorkflowEngine) -> None:
        """Persiste de forma segura el estado de ejecución si hay almacén configurado."""
        if self.persistence_store and hasattr(self.persistence_store, "save_workflow_execution"):
            try:
                self.persistence_store.save_workflow_execution(engine.get_execution())
            except Exception as exc:
                logger.warning(f"Error al persistir ejecución en scheduler: {exc}")

    def _run_loop(self) -> None:
        """Bucle continuo ejecutado en el hilo en segundo plano."""
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                logger.error(f"Error inesperado en ciclo de WorkflowScheduler: {exc}", exc_info=True)
            self._stop_event.wait(timeout=self.interval_seconds)
