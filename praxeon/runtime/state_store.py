"""Almacén de estado y sesiones para recuperación atómica (runtime/state_store.py)."""

from typing import Any, Dict, List, Optional
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

    def save(self, checkpoint: Checkpoint) -> None:
        """Alias para save_checkpoint."""
        self.save_checkpoint(checkpoint)

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Almacena una instantánea atómica de checkpoint."""
        self._checkpoints[checkpoint.id] = checkpoint
        sid = checkpoint.session_id or ""
        if sid:
            if sid not in self._session_checkpoints:
                self._session_checkpoints[sid] = []
            if checkpoint.id not in self._session_checkpoints[sid]:
                self._session_checkpoints[sid].append(checkpoint.id)

    def get(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """Alias para get_checkpoint."""
        return self.get_checkpoint(checkpoint_id)

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

    def list(self, session_id: Optional[str] = None) -> List[Checkpoint]:
        """Alias para list_checkpoints."""
        return self.list_checkpoints(session_id)

    def list_checkpoints(self, session_id: Optional[str] = None) -> List[Checkpoint]:
        """Lista cronológicamente todos los checkpoints de una sesión o globalmente."""
        if session_id:
            cids = self._session_checkpoints.get(session_id, [])
            return [self._checkpoints[cid] for cid in cids if cid in self._checkpoints]
        return list(self._checkpoints.values())

    def create_checkpoint(
        self,
        session_id: str,
        label: Optional[str] = None,
        state: Optional[SessionState] = None,
    ) -> Checkpoint:
        """Crea y persiste un snapshot de checkpoint atómico para la sesión."""
        import uuid
        target_state = state or self.load_state(session_id)
        if not target_state:
            raise KeyError(f"Sesión '{session_id}' no encontrada para crear checkpoint.")

        existing_cids = self._session_checkpoints.get(session_id, [])
        chk_id = f"chk_{session_id}_{len(existing_cids)}_{uuid.uuid4().hex[:6]}"
        hash_val = target_state.compute_hash() if hasattr(target_state, "compute_hash") else ""
        checkpoint = Checkpoint(
            id=chk_id,
            session_id=session_id,
            step_index=len(target_state.steps),
            state_hash=hash_val,
            evidence_ids=[getattr(e, "id", getattr(e, "content_hash", "")) for e in getattr(target_state, "evidence", []) if hasattr(e, "id") or hasattr(e, "content_hash")],
            forbidden_tools=list(target_state.forbidden_tools),
            reason=label or "Punto de restauración",
            snapshot_data=target_state.to_snapshot(),
        )
        self.save_checkpoint(checkpoint)
        if hasattr(target_state, "checkpoint_ids"):
            target_state.checkpoint_ids.append(chk_id)
            self.save_state(target_state)
        return checkpoint

    def restore_checkpoint(
        self,
        session_id: str,
        checkpoint_id: str,
        culprit_tool: Optional[str] = None,
        reason: str = "Restauración de checkpoint",
    ) -> Optional[SessionState]:
        """Restaura el estado de la sesión al snapshot del checkpoint y lo persiste."""
        chk = self.get_checkpoint(checkpoint_id)
        if not chk or (chk.session_id and chk.session_id != session_id):
            return None

        restored_state = SessionState.from_snapshot(chk.snapshot_data)
        if culprit_tool:
            restored_state.forbid_tool(culprit_tool)

        self.save_state(restored_state)
        return restored_state

    def restore(
        self,
        session_id: str,
        checkpoint_id: str,
        culprit_tool: Optional[str] = None,
        reason: str = "Restauración de checkpoint",
    ) -> Optional[SessionState]:
        """Alias para restore_checkpoint."""
        return self.restore_checkpoint(session_id, checkpoint_id, culprit_tool=culprit_tool, reason=reason)


    def delete_session(self, session_id: str) -> bool:
        """Elimina una sesión y sus checkpoints asociados en memoria."""
        had_state = session_id in self._states
        self._states.pop(session_id, None)
        cids = self._session_checkpoints.pop(session_id, [])
        for cid in cids:
            self._checkpoints.pop(cid, None)
        getattr(self, "_missions", {}).pop(session_id, None)
        return had_state or bool(cids)

    def save_mission(self, mission: Dict[str, Any]) -> None:
        """Persiste el estado de una misión interactiva."""
        if not hasattr(self, "_missions"):
            self._missions = {}
        sid = mission.get("session_id")
        if sid:
            self._missions[sid] = dict(mission)

    def load_mission(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Recupera el estado persistido de una misión."""
        if not hasattr(self, "_missions"):
            self._missions = {}
        return self._missions.get(session_id)

    def list_missions(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lista misiones filtradas opcionalmente por estado."""
        if not hasattr(self, "_missions"):
            self._missions = {}
        all_m = list(self._missions.values())
        if status:
            return [m for m in all_m if m.get("status") == status]
        return all_m

    def reconcile_interrupted_missions(self, reason: Optional[str] = None) -> List[Dict[str, Any]]:
        """Reconcilia transaccionalmente misiones interrumpidas tras reinicio."""
        if not hasattr(self, "_missions"):
            self._missions = {}
        interrupted = []
        rec_reason = reason or "Process restart / ungraceful shutdown"
        for sid, m in self._missions.items():
            st = (m.get("status") or "").lower()
            if st in ("running", "in_progress", "paused", "active"):
                m["status"] = "Interrupted_Recovered"
                m["interrupted_at"] = "after_restart"
                m["reconciliation_reason"] = rec_reason
                interrupted.append(dict(m))
        return interrupted

    def clear(self) -> None:
        """Limpia todos los estados y checkpoints."""
        self._states.clear()
        self._checkpoints.clear()
        self._session_checkpoints.clear()
        if hasattr(self, "_missions"):
            self._missions.clear()


class SqliteStateStore(StateStore, CheckpointStore):
    """Almacén durable y atómico basado en SQLite para estados de sesión y checkpoints.
    
    Garantiza la preservación transaccional de sesiones y árboles de checkpoints
    sobreviviendo a fallos o reinicios del proceso ejecutor.
    """

    def __init__(self, db_path: Optional[str] = None):
        import os
        import threading
        from praxeon.config import resolve_db_path

        if db_path == ":memory:":
            self.db_path = ":memory:"
        else:
            self.db_path = str(resolve_db_path("state.db", db_path))
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
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS missions (
                            session_id TEXT PRIMARY KEY,
                            status TEXT NOT NULL,
                            current_step INTEGER NOT NULL,
                            max_steps INTEGER NOT NULL,
                            mission_json TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        """
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_missions_status ON missions (status);"
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

    def save(self, checkpoint: Checkpoint) -> None:
        """Alias para save_checkpoint."""
        self.save_checkpoint(checkpoint)

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

    def get(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """Alias para get_checkpoint."""
        return self.get_checkpoint(checkpoint_id)

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

    def list(self, session_id: Optional[str] = None) -> List[Checkpoint]:
        """Alias para list_checkpoints."""
        return self.list_checkpoints(session_id)

    def list_checkpoints(self, session_id: Optional[str] = None) -> List[Checkpoint]:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                if session_id:
                    cur.execute(
                        "SELECT checkpoint_json FROM checkpoints WHERE session_id = ? ORDER BY sequence_num ASC",
                        (session_id,),
                    )
                else:
                    cur.execute(
                        "SELECT checkpoint_json FROM checkpoints ORDER BY sequence_num ASC"
                    )
                rows = cur.fetchall()
                return [Checkpoint.model_validate_json(r[0]) for r in rows]
            finally:
                self._close_conn(conn)

    def create_checkpoint(
        self,
        session_id: str,
        label: Optional[str] = None,
        state: Optional[SessionState] = None,
    ) -> Checkpoint:
        """Crea y persiste un snapshot de checkpoint atómico para la sesión en SQLite."""
        import uuid
        target_state = state or self.load_state(session_id)
        if not target_state:
            raise KeyError(f"Sesión '{session_id}' no encontrada para crear checkpoint.")

        existing_chks = self.list_checkpoints(session_id)
        chk_id = f"chk_{session_id}_{len(existing_chks)}_{uuid.uuid4().hex[:6]}"
        hash_val = target_state.compute_hash() if hasattr(target_state, "compute_hash") else ""
        checkpoint = Checkpoint(
            id=chk_id,
            session_id=session_id,
            step_index=len(target_state.steps),
            state_hash=hash_val,
            evidence_ids=[getattr(e, "id", getattr(e, "content_hash", "")) for e in getattr(target_state, "evidence", []) if hasattr(e, "id") or hasattr(e, "content_hash")],
            forbidden_tools=list(target_state.forbidden_tools),
            reason=label or "Punto de restauración",
            snapshot_data=target_state.to_snapshot(),
        )
        self.save_checkpoint(checkpoint)
        if hasattr(target_state, "checkpoint_ids"):
            target_state.checkpoint_ids.append(chk_id)
            self.save_state(target_state)
        return checkpoint

    def restore_checkpoint(
        self,
        session_id: str,
        checkpoint_id: str,
        culprit_tool: Optional[str] = None,
        reason: str = "Restauración de checkpoint",
    ) -> Optional[SessionState]:
        """Restaura el estado de la sesión al snapshot del checkpoint y lo persiste en SQLite."""
        chk = self.get_checkpoint(checkpoint_id)
        if not chk or (chk.session_id and chk.session_id != session_id):
            return None

        restored_state = SessionState.from_snapshot(chk.snapshot_data)
        if culprit_tool:
            restored_state.forbid_tool(culprit_tool)

        self.save_state(restored_state)
        return restored_state

    def restore(
        self,
        session_id: str,
        checkpoint_id: str,
        culprit_tool: Optional[str] = None,
        reason: str = "Restauración de checkpoint",
    ) -> Optional[SessionState]:
        """Alias para restore_checkpoint."""
        return self.restore_checkpoint(session_id, checkpoint_id, culprit_tool=culprit_tool, reason=reason)


    def delete_session(self, session_id: str) -> bool:
        """Elimina una sesión y sus checkpoints asociados de la base de datos SQLite."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute("DELETE FROM checkpoints WHERE session_id = ?", (session_id,))
                    conn.execute("DELETE FROM missions WHERE session_id = ?", (session_id,))
                    cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
                    return cur.rowcount > 0
            finally:
                self._close_conn(conn)

    def save_mission(self, mission: Dict[str, Any]) -> None:
        """Persiste transaccionalmente el estado completo de una misión en SQLite (REC-01)."""
        import datetime
        import json
        sid = mission.get("session_id")
        if not sid:
            return
        status = str(mission.get("status") or "running")
        current_step = int(mission.get("current_step") or 0)
        max_steps = int(mission.get("max_steps") or 25)
        raw_json = json.dumps(mission)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO missions (session_id, status, current_step, max_steps, mission_json, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (sid, status, current_step, max_steps, raw_json, now),
                    )
            finally:
                self._close_conn(conn)

    def load_mission(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Carga el estado persistido de una misión por su ID."""
        import json
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT mission_json FROM missions WHERE session_id = ?", (session_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return json.loads(row[0])
            finally:
                self._close_conn(conn)

    def list_missions(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lista misiones persistidas en SQLite."""
        import json
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                if status:
                    cur.execute("SELECT mission_json FROM missions WHERE status = ? ORDER BY updated_at DESC", (status,))
                else:
                    cur.execute("SELECT mission_json FROM missions ORDER BY updated_at DESC")
                return [json.loads(row[0]) for row in cur.fetchall()]
            finally:
                self._close_conn(conn)

    def reconcile_interrupted_missions(self, reason: Optional[str] = None) -> List[Dict[str, Any]]:
        """Reconcilia transaccionalmente misiones interrumpidas detectadas tras reiniciar el proceso (REC-01)."""
        import datetime
        import json
        interrupted = []
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        rec_reason = reason or "Process restart / ungraceful shutdown"
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT session_id, mission_json FROM missions WHERE status IN ('Running', 'running', 'in_progress', 'Paused', 'paused', 'active')"
                    )
                    rows = cur.fetchall()
                    for sid, m_json in rows:
                        data = json.loads(m_json)
                        data["status"] = "Interrupted_Recovered"
                        data["interrupted_at"] = now
                        data["reconciliation_reason"] = rec_reason
                        updated_json = json.dumps(data)
                        conn.execute(
                            "UPDATE missions SET status = 'Interrupted_Recovered', mission_json = ?, updated_at = ? WHERE session_id = ?",
                            (updated_json, now, sid),
                        )
                        interrupted.append(data)
            finally:
                self._close_conn(conn)
        return interrupted

    def clear(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute("DELETE FROM sessions;")
                    conn.execute("DELETE FROM checkpoints;")
                    conn.execute("DELETE FROM missions;")
            finally:
                self._close_conn(conn)
