"""Bus de eventos persistente y reactivo para PRAXEON 1.0 (praxeon/runtime/event_bus.py).

Especificación PRAXEON 1.0 (Sección 4).
Proporciona:
1. Persistencia transaccional durable en SQLite con WAL (Write-Ahead Logging).
2. Generación y garantía de secuencias monótonas crecientes por sesión.
3. Despacho reactivo en memoria (Pub/Sub) para WebSockets, SSE y reducers.
4. Consultas paginadas resistentes a reconexiones (gap recovery).
"""

import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Dict, List, Optional, Set
import uuid

from praxeon.domain.events import EventType, RuntimeEvent, make_event


class EventStore:
    """Almacén durable de eventos basado en SQLite WAL."""

    def __init__(self, db_path: str = ".jev_cache/events.db"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            if not hasattr(self, "_mem_conn") or self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _close_conn(self, conn: sqlite3.Connection) -> None:
        if self.db_path != ":memory:":
            conn.close()

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
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
                        """
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_events_session ON runtime_events (session_id, sequence);"
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_events_type ON runtime_events (type);"
                    )
            finally:
                self._close_conn(conn)

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
        payload: Dict[str, Any],
        parent_id: Optional[str] = None,
        node_id: Optional[str] = None,
        decision_id: Optional[str] = None,
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
    ) -> List[RuntimeEvent]:
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
                results: List[RuntimeEvent] = []
                for row in rows:
                    (
                        eid, sid, seq, ev_type, ts_str,
                        nid, pid, did, payload_raw
                    ) = row
                    try:
                        p_dict = json.loads(payload_raw)
                    except Exception:
                        p_dict = {}
                    try:
                        parsed_type = EventType(ev_type)
                    except Exception:
                        parsed_type = ev_type  # Fallback a string si fuese un tipo extendido
                    try:
                        ts = datetime.fromisoformat(ts_str)
                    except Exception:
                        ts = datetime.utcnow()

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
    ) -> List[RuntimeEvent]:
        """Alias explícito para recuperación de eventos posteriores a una secuencia (gap recovery)."""
        return self.get_events(session_id, after_sequence=after_sequence, limit=limit)

    def get_all_events(self, session_id: str) -> List[RuntimeEvent]:
        """Devuelve el stream completo de eventos para una sesión."""
        return self.get_events(session_id, after_sequence=0, limit=100_000)

    def list_sessions(self) -> List[str]:
        """Obtiene la lista de identificadores de sesiones registradas."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT DISTINCT session_id FROM runtime_events ORDER BY rowid DESC")
                return [row[0] for row in cur.fetchall()]
            finally:
                self._close_conn(conn)

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

    def __init__(self, store: Optional[EventStore] = None):
        self.store = store or EventStore()
        self._subscribers: Set[Callable[[RuntimeEvent], Any]] = set()
        self._session_subscribers: Dict[str, Set[Callable[[RuntimeEvent], Any]]] = {}
        self._async_queues: Set[asyncio.Queue] = set()
        self._async_session_queues: Dict[str, Set[asyncio.Queue]] = {}
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
        session_id: Optional[str] = None,
    ) -> Callable[[], None]:
        """Registra un observador síncrono. Devuelve una función para cancelar la suscripción."""
        with self._lock:
            if session_id:
                if session_id not in self._session_subscribers:
                    self._session_subscribers[session_id] = set()
                self._session_subscribers[session_id].add(callback)
            else:
                self._subscribers.add(callback)

        def unsubscribe():
            with self._lock:
                if session_id and session_id in self._session_subscribers:
                    self._session_subscribers[session_id].discard(callback)
                self._subscribers.discard(callback)

        return unsubscribe

    def register_async_queue(
        self,
        queue: asyncio.Queue,
        session_id: Optional[str] = None,
    ) -> Callable[[], None]:
        """Registra una asyncio.Queue para streaming WebSocket o SSE con aislamiento por sesión."""
        with self._lock:
            if session_id:
                if session_id not in self._async_session_queues:
                    self._async_session_queues[session_id] = set()
                self._async_session_queues[session_id].add(queue)
            else:
                self._async_queues.add(queue)

        def unregister():
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
        node_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
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
            except Exception:
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
                pass  # Evitar que errores de observadores rompan el flujo del runtime

        # 3. Despacho a colas asíncronas
        for q in async_queues:
            try:
                q.put_nowait(persisted_event)
            except Exception:
                pass

        return persisted_event

    def get_events(self, session_id: str, after_sequence: int = 0, limit: int = 500) -> List[RuntimeEvent]:
        return self.store.get_events(session_id, after_sequence=after_sequence, limit=limit)

    def get_events_after(self, session_id: str, after_sequence: int = 0, limit: int = 500) -> List[RuntimeEvent]:
        """Recupera eventos para gap recovery tras reconexión."""
        return self.store.get_events_after(session_id, after_sequence=after_sequence, limit=limit)

    def get_all_events(self, session_id: str) -> List[RuntimeEvent]:
        return self.store.get_all_events(session_id)

    def list_sessions(self) -> List[str]:
        return self.store.list_sessions()

    def delete_session(self, session_id: str) -> int:
        """Purga todos los eventos de la sesión y desconecta observadores asociados."""
        count = self.store.delete_session_events(session_id)
        with self._lock:
            self._session_subscribers.pop(session_id, None)
            self._async_session_queues.pop(session_id, None)
        return count

