"""Bus de eventos persistente y reactivo para PRAXEON 1.0 (praxeon/runtime/event_bus.py).

Especificación PRAXEON 1.0 (Sección 4).
Proporciona:
1. Persistencia transaccional durable en SQLite con WAL (Write-Ahead Logging).
2. Generación y garantía de secuencias monótonas crecientes por sesión.
3. Despacho reactivo en memoria (Pub/Sub) para WebSockets, SSE y reducers.
4. Consultas paginadas resistentes a reconexiones (gap recovery).
"""

import asyncio
from datetime import datetime, UTC
import json

import threading
from typing import Any, Optional
from collections.abc import Callable

from praxeon.domain.events import EventType, RuntimeEvent, make_event
import logging

from praxeon.sqlite_base import SqliteStore

logger = logging.getLogger(__name__)


class EventStore(SqliteStore):
    """Almacén durable de eventos basado en SQLite WAL."""

    DEFAULT_DB_NAME = 'events.db'
    MIGRATIONS = (
        (   # versión 1: esquema histórico (IF NOT EXISTS: lo aceptan las bases creadas antes de las migraciones)
            """
            CREATE TABLE IF NOT EXISTS runtime_events (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                node_id TEXT,
                parent_id TEXT,
                decision_id TEXT,
                payload TEXT NOT NULL,
                UNIQUE(session_id, sequence)
            );
            """,
            'CREATE INDEX IF NOT EXISTS idx_events_session ON runtime_events (session_id, sequence);',
            'CREATE INDEX IF NOT EXISTS idx_events_type ON runtime_events (type);',
        ),
    )


    def next_sequence(self, session_id: str) -> int:
        """Calcula de forma segura el siguiente número de secuencia monótono para una sesión."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM runtime_events WHERE session_id = ?",
                    (session_id,),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 1
            finally:
                self._close_conn(conn)

    def append(self, event: RuntimeEvent) -> RuntimeEvent:
        """Persiste un evento asignando secuencia de forma estrictamente atómica dentro de la transacción."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    cur = conn.cursor()
                    seq = event.sequence
                    if seq <= 0:
                        cur.execute(
                            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM runtime_events WHERE session_id = ?",
                            (event.session_id,),
                        )
                        row = cur.fetchone()
                        seq = int(row[0]) if row else 1

                    event_to_save = event.model_copy(update={"sequence": seq}) if seq != event.sequence else event
                    payload_json = json.dumps(event_to_save.payload, default=str)
                    type_val = event_to_save.type.value if isinstance(event_to_save.type, EventType) else str(event_to_save.type)
                    ts_str = event_to_save.timestamp.isoformat()

                    cur.execute(
                        """
                        INSERT INTO runtime_events (
                            event_id, session_id, sequence, type, timestamp,
                            node_id, parent_id, decision_id, payload
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_to_save.event_id,
                            event_to_save.session_id,
                            event_to_save.sequence,
                            type_val,
                            ts_str,
                            event_to_save.node_id,
                            event_to_save.parent_id,
                            event_to_save.decision_id,
                            payload_json,
                        ),
                    )
                return event_to_save
            finally:
                self._close_conn(conn)

    def append_new(
        self,
        session_id: str,
        type: EventType,
        payload: dict[str, Any],
        parent_id: str | None = None,
        node_id: str | None = None,
        decision_id: str | None = None,
    ) -> RuntimeEvent:
        """Crea y persiste un nuevo evento asignando sequence estrictamente atómico en la transacción (Sección 4.3)."""
        ev = make_event(
            session_id=session_id,
            sequence=0,
            event_type=type,
            node_id=node_id,
            parent_id=parent_id,
            decision_id=decision_id,
            payload=payload,
        )
        return self.append(ev)

    def get_events(
        self,
        session_id: str,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[RuntimeEvent]:
        """Recupera eventos ordenados por secuencia monótona estricta."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT event_id, session_id, sequence, type, timestamp,
                           node_id, parent_id, decision_id, payload
                    FROM runtime_events
                    WHERE session_id = ? AND sequence > ?
                    ORDER BY sequence ASC
                    LIMIT ?
                    """,
                    (session_id, after_sequence, limit),
                )
                rows = cur.fetchall()
                results: list[RuntimeEvent] = []
                for row in rows:
                    (
                        eid, sid, seq, ev_type, ts_str,
                        nid, pid, did, payload_raw
                    ) = row
                    try:
                        p_dict = json.loads(payload_raw)
                    except (ValueError, TypeError, RecursionError):  # payload ilegible: el evento se devuelve sin datos
                        p_dict = {}
                    try:
                        parsed_type = EventType(ev_type)
                    except ValueError:  # tipo de evento extendido o desconocido: se conserva como texto
                        parsed_type = ev_type  # Fallback a string si fuese un tipo extendido
                    try:
                        ts = datetime.fromisoformat(ts_str)
                    except (ValueError, TypeError):  # fecha ilegible: se usa la hora actual
                        ts = datetime.now(UTC)

                    results.append(

                        RuntimeEvent(
                            event_id=eid,
                            session_id=sid,
                            sequence=seq,
                            type=parsed_type,
                            timestamp=ts,
                            node_id=nid,
                            parent_id=pid,
                            decision_id=did,
                            payload=p_dict,
                        )
                    )
                return results
            finally:
                self._close_conn(conn)

    def get_events_after(
        self,
        session_id: str,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[RuntimeEvent]:
        """Alias explícito para recuperación de eventos posteriores a una secuencia (gap recovery)."""
        return self.get_events(session_id, after_sequence=after_sequence, limit=limit)

    def get_all_events(self, session_id: str) -> list[RuntimeEvent]:
        """Devuelve el stream completo de eventos para una sesión."""
        return self.get_events(session_id, after_sequence=0, limit=100_000)

    def list_sessions(self) -> list[str]:
        """Obtiene la lista de identificadores de sesiones registradas."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT DISTINCT session_id FROM runtime_events ORDER BY rowid DESC")
                return [row[0] for row in cur.fetchall()]
            finally:
                self._close_conn(conn)

    # Contadores del resumen de una sesión por SQL (AUD-20, P-04): antes se cargaban y deserializaban todos los eventos y
    # se reconstruía el árbol entero solo para contar. Una pasada por los eventos; el JSON solo se lee en decisiones y
    # acciones propuestas (payloads pequeños), nunca en las salidas de herramientas. _NODES_SQL reproduce las reglas de
    # TreeReducer: la raíz (session.started), cada acción propuesta y cada rama de política con nodo y padre distintos.
    _COUNTERS_SQL = """
        SELECT COUNT(*),
               COALESCE(SUM(type = 'policy.decided'), 0),
               COALESCE(SUM(status = 'ALLOW'), 0),
               COALESCE(SUM(status = 'BLOCK'), 0),
               COALESCE(SUM(status = 'REVIEW'), 0),
               COALESCE(SUM(type = 'approval.requested'), 0),
               MAX(sequence)
        FROM (
            SELECT type, sequence,
                   CASE WHEN type = 'policy.decided' AND json_valid(payload)
                        THEN UPPER(CAST(json_extract(payload, '$.status') AS TEXT)) END AS status
            FROM runtime_events WHERE session_id = ?
        )
    """
    _NODES_SQL = """
        SELECT COUNT(DISTINCT nid) FROM (
            SELECT CASE
                WHEN type = 'session.started' THEN COALESCE(NULLIF(node_id, ''), 'root_' || session_id)
                WHEN type = 'action.proposed' THEN COALESCE(NULLIF(node_id, ''), 'node_' ||
                    CASE WHEN NOT json_valid(payload) THEN CAST(sequence AS TEXT)
                         ELSE CASE json_type(payload, '$.action_id')
                            WHEN 'null' THEN 'None' WHEN 'true' THEN 'True' WHEN 'false' THEN 'False'
                            ELSE COALESCE(CAST(json_extract(payload, '$.action_id') AS TEXT), CAST(sequence AS TEXT)) END
                    END)
                ELSE node_id END AS nid
            FROM runtime_events
            WHERE session_id = ? AND (type IN ('session.started', 'action.proposed') OR (
                type = 'policy.decided' AND NULLIF(node_id, '') IS NOT NULL AND NULLIF(parent_id, '') IS NOT NULL
                AND node_id != parent_id))
        )
    """

    def session_counters(self, session_id: str) -> dict[str, Any]:
        """Contadores del resumen de una sesión: eventos, decisiones por estado, aprobaciones, nodos y último evento."""
        with self._lock:
            conn = self._get_connection()
            try:
                events, decisions, allowed, blocked, review, waiting, last_seq = conn.execute(
                    self._COUNTERS_SQL, (session_id,)).fetchone()
                nodes = conn.execute(self._NODES_SQL, (session_id,)).fetchone()[0]
                last_row = conn.execute("SELECT timestamp FROM runtime_events WHERE session_id = ? AND sequence = ?",
                                        (session_id, last_seq)).fetchone() if last_seq is not None else None
            finally:
                self._close_conn(conn)
        last_ts = last_row[0] if last_row else None
        last: datetime | None = None
        if last_ts:
            try:
                last = datetime.fromisoformat(last_ts)
            except ValueError:
                last = None
        return {"event_count": events, "total_decisions": decisions, "allowed_count": allowed, "blocked_count": blocked,
                "review_count": review, "waiting_count": waiting, "node_count": nodes, "last_event_at": last}

    def delete_session_events(self, session_id: str) -> int:
        """Elimina todos los eventos persistidos para una sesión."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    cur = conn.cursor()
                    cur.execute("DELETE FROM runtime_events WHERE session_id = ?", (session_id,))
                    return cur.rowcount
            finally:
                self._close_conn(conn)


class EventBus:
    """Bus reactivo en memoria con persistencia integrada y soporte síncrono/asíncrono."""

    _instance: Optional["EventBus"] = None
    _singleton_lock = threading.Lock()

    def __init__(self, store: EventStore | None = None):
        self.store = store or EventStore()
        self._subscribers: set[Callable[[RuntimeEvent], Any]] = set()
        self._session_subscribers: dict[str, set[Callable[[RuntimeEvent], Any]]] = {}
        self._async_queues: set[asyncio.Queue[Any]] = set()
        self._async_session_queues: dict[str, set[asyncio.Queue[Any]]] = {}
        self._lock = threading.Lock()

    @classmethod
    def get_default(cls) -> "EventBus":
        """Singleton thread-safe para compartir el bus en todo el proceso."""
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def set_default(cls, bus: Optional["EventBus"]) -> None:
        with cls._singleton_lock:
            cls._instance = bus

    def subscribe(
        self,
        callback: Callable[[RuntimeEvent], Any],
        session_id: str | None = None,
    ) -> Callable[[], None]:
        """Registra un observador síncrono. Devuelve una función para cancelar la suscripción."""
        with self._lock:
            if session_id:
                if session_id not in self._session_subscribers:
                    self._session_subscribers[session_id] = set()
                self._session_subscribers[session_id].add(callback)
            else:
                self._subscribers.add(callback)

        def unsubscribe() -> None:
            with self._lock:
                if session_id and session_id in self._session_subscribers:
                    self._session_subscribers[session_id].discard(callback)
                self._subscribers.discard(callback)

        return unsubscribe

    def register_async_queue(
        self,
        queue: asyncio.Queue[Any],
        session_id: str | None = None,
    ) -> Callable[[], None]:
        """Registra una asyncio.Queue para streaming WebSocket o SSE con aislamiento por sesión."""
        with self._lock:
            if session_id:
                if session_id not in self._async_session_queues:
                    self._async_session_queues[session_id] = set()
                self._async_session_queues[session_id].add(queue)
            else:
                self._async_queues.add(queue)

        def unregister() -> None:
            with self._lock:
                if session_id and session_id in self._async_session_queues:
                    self._async_session_queues[session_id].discard(queue)
                    if not self._async_session_queues[session_id]:
                        del self._async_session_queues[session_id]
                self._async_queues.discard(queue)

        return unregister

    def emit(
        self,
        session_id: str,
        event_type: EventType,
        node_id: str | None = None,
        parent_id: str | None = None,
        decision_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        """Crea, numera atómicamente, persiste y distribuye un nuevo evento."""
        ev = make_event(
            session_id=session_id,
            sequence=0,
            event_type=event_type,
            node_id=node_id,
            parent_id=parent_id,
            decision_id=decision_id,
            payload=payload or {},
        )
        return self.publish(ev)

    def publish(self, event: Any) -> RuntimeEvent:
        """Persiste y despacha el evento a todos los suscriptores registrados, con soporte para RuntimeEvent y TelemetryEvent."""
        if not isinstance(event, RuntimeEvent):
            ev_type_raw = getattr(event, "event_type", getattr(event, "type", "custom_event"))
            try:
                ev_type = EventType(ev_type_raw)
            except ValueError:  # tipo de evento con nombre antiguo: se traduce con la tabla de abajo
                type_map = {
                    "policy_decision": EventType.POLICY_DECIDED,
                    "tool_execution": EventType.EXECUTION_COMPLETED,
                    "observation_captured": EventType.OBSERVATION_RECORDED,
                    "supervisor_intervention": EventType.INTERVENTION_APPLIED,
                }
                ev_type = type_map.get(str(ev_type_raw), EventType.ACTION_PROPOSED)

            payload_dict = event.model_dump() if hasattr(event, "model_dump") else (event.__dict__ if hasattr(event, "__dict__") else {})
            actual_event = make_event(
                session_id=getattr(event, "session_id", "default_session"),
                sequence=getattr(event, "sequence", 0),
                event_type=ev_type,
                node_id=getattr(event, "node_id", getattr(event, "action_id", None)),
                decision_id=getattr(event, "decision_id", None),
                payload=payload_dict,
            )
        else:
            actual_event = event

        # 1. Persistencia durable (asigna sequence atómicamente si venía en 0)
        persisted_event = self.store.append(actual_event)

        # 2. Despacho a callbacks en memoria
        targets = set()
        with self._lock:
            targets.update(self._subscribers)
            if persisted_event.session_id in self._session_subscribers:
                targets.update(self._session_subscribers[persisted_event.session_id])
            async_queues = list(self._async_queues)
            if persisted_event.session_id in self._async_session_queues:
                async_queues.extend(self._async_session_queues[persisted_event.session_id])

        for cb in targets:
            try:
                cb(persisted_event)
            except Exception:
                logger.warning('Un observador del bus de eventos falló', exc_info=True)

        # 3. Despacho a colas asíncronas
        for q in async_queues:
            try:
                q.put_nowait(persisted_event)
            except Exception:
                logger.debug('Cola asíncrona de eventos llena o cerrada', exc_info=True)

        return persisted_event

    def get_events(self, session_id: str, after_sequence: int = 0, limit: int = 500) -> list[RuntimeEvent]:
        return self.store.get_events(session_id, after_sequence=after_sequence, limit=limit)

    def get_events_after(self, session_id: str, after_sequence: int = 0, limit: int = 500) -> list[RuntimeEvent]:
        """Recupera eventos para gap recovery tras reconexión."""
        return self.store.get_events_after(session_id, after_sequence=after_sequence, limit=limit)

    def get_all_events(self, session_id: str) -> list[RuntimeEvent]:
        return self.store.get_all_events(session_id)

    def list_sessions(self) -> list[str]:
        return self.store.list_sessions()

    def session_counters(self, session_id: str) -> dict[str, Any]:
        """Contadores del resumen de una sesión, calculados por el almacén (``EventStore.session_counters``)."""
        return self.store.session_counters(session_id)

    def delete_session(self, session_id: str) -> int:
        """Purga todos los eventos de la sesión y desconecta observadores asociados."""
        count = self.store.delete_session_events(session_id)
        with self._lock:
            self._session_subscribers.pop(session_id, None)
            self._async_session_queues.pop(session_id, None)
        return count

