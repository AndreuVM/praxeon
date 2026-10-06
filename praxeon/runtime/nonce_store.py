"""Almacenamiento durable y en memoria de nonces y capabilities consumidas (runtime/nonce_store.py).

Garantiza la prevención estricta de ataques de repetición (replay attacks) tanto en un
proceso único como frente a reinicios o ejecución distribuida con TTL y poda automática.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import threading
from typing import Dict, Optional, Tuple


def _is_expired(exp: Optional[datetime], now: Optional[datetime] = None) -> bool:
    if not exp:
        return False
    current = now or datetime.now(timezone.utc)
    if exp.tzinfo is None and current.tzinfo is not None:
        exp = exp.replace(tzinfo=timezone.utc)
    elif exp.tzinfo is not None and current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current > exp



class NonceStore(ABC):
    """Interfaz abstracta para almacenes de nonces consumidos."""

    @abstractmethod
    def has_been_consumed(self, decision_id: str, nonce: str) -> bool:
        """Verifica si un par decision_id:nonce ya ha sido registrado como consumido."""
        pass

    @abstractmethod
    def consume(self, decision_id: str, nonce: str, expires_at: Optional[datetime] = None) -> bool:
        """Marca un capability como consumido. Devuelve True si se registró con éxito, False si ya existía."""
        pass

    @abstractmethod
    def prune_expired(self) -> int:
        """Elimina entradas cuya fecha de expiración haya sido superada."""
        pass


class InMemoryNonceStore(NonceStore):
    """Almacén concurrente y seguro en memoria para nonces con poda por TTL."""

    def __init__(self):
        self._lock = threading.Lock()
        # Clave: f"{decision_id}:{nonce}", Valor: Optional[datetime]
        self._store: Dict[str, Optional[datetime]] = {}

    def _make_key(self, decision_id: str, nonce: str) -> str:
        return f"{decision_id}:{nonce}"

    def has_been_consumed(self, decision_id: str, nonce: str) -> bool:
        key = self._make_key(decision_id, nonce)
        with self._lock:
            if key not in self._store:
                return False
            exp = self._store[key]
            if _is_expired(exp):
                del self._store[key]
                return False
            return True

    def consume(self, decision_id: str, nonce: str, expires_at: Optional[datetime] = None) -> bool:
        key = self._make_key(decision_id, nonce)
        with self._lock:
            if key in self._store:
                exp = self._store[key]
                if _is_expired(exp):
                    pass  # Expirado, permitimos sobreescritura si correspondiese
                else:
                    return False  # Ya consumido y activo
            self._store[key] = expires_at
            return True

    def prune_expired(self) -> int:
        now = datetime.now(timezone.utc)
        removed = 0
        with self._lock:
            expired_keys = [k for k, exp in self._store.items() if _is_expired(exp, now)]
            for k in expired_keys:
                del self._store[k]
                removed += 1
        return removed


    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


class SqliteNonceStore(NonceStore):
    """Almacén durable y transaccional basado en SQLite para prevención de replay attacks.
    
    Persiste capabilities y nonces consumidos en disco, sobreviviendo a reinicios
    del proceso y permitiendo sincronización concurrente entre múltiples procesos.
    """

    def __init__(self, db_path: Optional[str] = None):
        import os
        import sqlite3
        from pathlib import Path
        from praxeon.config import resolve_db_path

        if db_path == ":memory:":
            self.db_path = ":memory:"
        else:
            self.db_path = str(resolve_db_path("nonces.db", db_path))
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

            
        self._lock = threading.Lock()
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
                        CREATE TABLE IF NOT EXISTS consumed_nonces (
                            decision_id TEXT NOT NULL,
                            nonce TEXT NOT NULL,
                            expires_at TEXT,
                            created_at TEXT NOT NULL,
                            PRIMARY KEY (decision_id, nonce)
                        );
                        """
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_expires_at ON consumed_nonces (expires_at);"
                    )
            finally:
                self._close_conn(conn)

    def has_been_consumed(self, decision_id: str, nonce: str) -> bool:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT expires_at FROM consumed_nonces WHERE decision_id = ? AND nonce = ?",
                    (decision_id, nonce),
                )
                row = cur.fetchone()
                if not row:
                    return False
                
                exp_str = row[0]
                if exp_str:
                    try:
                        exp = datetime.fromisoformat(exp_str)
                        if _is_expired(exp):
                            # Ha expirado: podar y permitir
                            with conn:
                                cur.execute(
                                    "DELETE FROM consumed_nonces WHERE decision_id = ? AND nonce = ?",
                                    (decision_id, nonce),
                                )
                            return False
                    except Exception:
                        pass
                return True
            finally:
                self._close_conn(conn)

    def consume(self, decision_id: str, nonce: str, expires_at: Optional[datetime] = None) -> bool:
        import sqlite3
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        exp_str = expires_at.isoformat() if expires_at else None

        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                # 1. Intentar inserción atómica inicial (falla si ya existe por PRIMARY KEY)
                try:
                    with conn:
                        cur.execute(
                            """
                            INSERT INTO consumed_nonces (decision_id, nonce, expires_at, created_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (decision_id, nonce, exp_str, now_str),
                        )
                    return True
                except sqlite3.IntegrityError:
                    # 2. Conflicto de clave primaria: ya existe.
                    # Comprobar atómicamente si estaba expirado y actualizar solo en ese caso.
                    with conn:
                        cur.execute(
                            """
                            UPDATE consumed_nonces
                            SET expires_at = ?, created_at = ?
                            WHERE decision_id = ? AND nonce = ?
                              AND expires_at IS NOT NULL
                              AND expires_at < ?
                            """,
                            (exp_str, now_str, decision_id, nonce, now_str),
                        )
                        if cur.rowcount > 0:
                            return True
                    return False
            finally:
                self._close_conn(conn)

    def prune_expired(self) -> int:
        now_str = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    cur = conn.cursor()
                    cur.execute(
                        "DELETE FROM consumed_nonces WHERE expires_at IS NOT NULL AND expires_at < ?",
                        (now_str,),
                    )
                    return cur.rowcount
            finally:

                self._close_conn(conn)

    def __len__(self) -> int:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM consumed_nonces")
                row = cur.fetchone()
                return row[0] if row else 0
            finally:
                self._close_conn(conn)
