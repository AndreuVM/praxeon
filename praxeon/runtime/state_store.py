"""Almacén de estado y sesiones para recuperación atómica (runtime/state_store.py)."""

from typing import Dict, List, Optional
from praxeon.domain.interfaces import CheckpointStore, StateStore
from praxeon.domain.models import Checkpoint
from praxeon.runtime.state import SessionState


class InMemoryStateStore(StateStore, CheckpointStore):
    """Implementación en memoria de almacén de estados y checkpoints de sesión."""

    def __init__(self):
        self._states: Dict[str, SessionState] = {}
        self._checkpoints: Dict[str, Checkpoint] = {}
        self._session_checkpoints: Dict[str, List[str]] = {}

    def save_state(self, state: SessionState) -> None:
        """Guarda o actualiza el estado de la sesión."""
        self._states[state.session_id] = state

    def load_state(self, session_id: str) -> Optional[SessionState]:
        """Recupera el estado de una sesión por su identificador."""
        return self._states.get(session_id)

    def list_sessions(self) -> List[str]:
        """Devuelve la lista de IDs de sesiones conocidas."""
        return list(self._states.keys())

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Almacena una instantánea atómica de checkpoint."""
        self._checkpoints[checkpoint.id] = checkpoint
        if checkpoint.session_id not in self._session_checkpoints:
            self._session_checkpoints[checkpoint.session_id] = []
        self._session_checkpoints[checkpoint.session_id].append(checkpoint.id)

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """Obtiene un checkpoint por su ID."""
        return self._checkpoints.get(checkpoint_id)

    def get_latest_checkpoint(self, session_id: Optional[str] = None) -> Optional[Checkpoint]:
        """Obtiene el último checkpoint registrado para una sesión o globalmente."""
        if session_id:
            cids = self._session_checkpoints.get(session_id, [])
            if not cids:
                return None
            return self._checkpoints.get(cids[-1])
        if not self._checkpoints:
            return None
        last_id = list(self._checkpoints.keys())[-1]
        return self._checkpoints.get(last_id)

    def list_checkpoints(self, session_id: str) -> List[Checkpoint]:
        """Lista cronológicamente todos los checkpoints de una sesión."""
        cids = self._session_checkpoints.get(session_id, [])
        return [self._checkpoints[cid] for cid in cids if cid in self._checkpoints]

    def delete_session(self, session_id: str) -> bool:
        """Elimina una sesión y sus checkpoints asociados en memoria."""
        had_state = session_id in self._states
        self._states.pop(session_id, None)
        cids = self._session_checkpoints.pop(session_id, [])
        for cid in cids:
            self._checkpoints.pop(cid, None)
        return had_state or bool(cids)

    def clear(self) -> None:
        """Limpia todos los estados y checkpoints."""
        self._states.clear()
        self._checkpoints.clear()
        self._session_checkpoints.clear()


class SqliteStateStore(StateStore, CheckpointStore):
    """Almacén durable y atómico basado en SQLite para estados de sesión y checkpoints.
    
    Garantiza la preservación transaccional de sesiones y árboles de checkpoints
    sobreviviendo a fallos o reinicios del proceso ejecutor.
    """

    def __init__(self, db_path: str = ".jev_cache/state.db"):
        import os
        import threading

        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
            
        self._lock = threading.Lock()
        self._seq = 0
        self._init_db()

    def _get_connection(self):
        import sqlite3
        if self.db_path == ":memory:":
            if not hasattr(self, "_mem_conn") or self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _close_conn(self, conn) -> None:
        if self.db_path != ":memory:":
            conn.close()

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS sessions (
                            session_id TEXT PRIMARY KEY,
                            state_json TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        """
                    )
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS checkpoints (
                            id TEXT PRIMARY KEY,
                            session_id TEXT NOT NULL,
                            sequence_num INTEGER NOT NULL,
                            checkpoint_json TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        );
                        """
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_chk_session ON checkpoints (session_id, sequence_num);"
                    )
            finally:
                self._close_conn(conn)

    def save_state(self, state: SessionState) -> None:
        import datetime
        with self._lock:
            conn = self._get_connection()
            try:
                raw_json = state.model_dump_json()
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                with conn:

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO sessions (session_id, state_json, updated_at)
                        VALUES (?, ?, ?)
                        """,
                        (state.session_id, raw_json, now),
                    )
            finally:
                self._close_conn(conn)

    def load_state(self, session_id: str) -> Optional[SessionState]:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT state_json FROM sessions WHERE session_id = ?", (session_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return SessionState.model_validate_json(row[0])
            finally:
                self._close_conn(conn)

    def list_sessions(self) -> List[str]:
        """Devuelve la lista de IDs de sesiones registradas en SQLite."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT session_id FROM sessions ORDER BY updated_at DESC")
                return [row[0] for row in cur.fetchall()]
            finally:
                self._close_conn(conn)

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        import datetime
        with self._lock:
            conn = self._get_connection()
            try:
                self._seq += 1
                raw_json = checkpoint.model_dump_json()
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                with conn:

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO checkpoints (id, session_id, sequence_num, checkpoint_json, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (checkpoint.id, checkpoint.session_id, self._seq, raw_json, now),
                    )
            finally:
                self._close_conn(conn)

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT checkpoint_json FROM checkpoints WHERE id = ?", (checkpoint_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return Checkpoint.model_validate_json(row[0])
            finally:
                self._close_conn(conn)

    def get_latest_checkpoint(self, session_id: Optional[str] = None) -> Optional[Checkpoint]:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                if session_id:
                    cur.execute(
                        "SELECT checkpoint_json FROM checkpoints WHERE session_id = ? ORDER BY sequence_num DESC LIMIT 1",
                        (session_id,),
                    )
                else:
                    cur.execute(
                        "SELECT checkpoint_json FROM checkpoints ORDER BY sequence_num DESC LIMIT 1"
                    )
                row = cur.fetchone()
                if not row:
                    return None
                return Checkpoint.model_validate_json(row[0])
            finally:
                self._close_conn(conn)

    def list_checkpoints(self, session_id: str) -> List[Checkpoint]:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT checkpoint_json FROM checkpoints WHERE session_id = ? ORDER BY sequence_num ASC",
                    (session_id,),
                )
                rows = cur.fetchall()
                return [Checkpoint.model_validate_json(r[0]) for r in rows]
            finally:
                self._close_conn(conn)

    def delete_session(self, session_id: str) -> bool:
        """Elimina una sesión y sus checkpoints asociados de la base de datos SQLite."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute("DELETE FROM checkpoints WHERE session_id = ?", (session_id,))
                    cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
                    return cur.rowcount > 0
            finally:
                self._close_conn(conn)

    def clear(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute("DELETE FROM sessions;")
                    conn.execute("DELETE FROM checkpoints;")
            finally:
                self._close_conn(conn)
