"""Almacenamiento durable y en memoria de nonces y capabilities consumidas (runtime/nonce_store.py).

Garantiza la prevención estricta de ataques de repetición (replay attacks) tanto en un
proceso único como frente a reinicios o ejecución distribuida con TTL y poda automática.
"""

from abc import ABC, abstractmethod
from datetime import datetime, UTC
import threading
import logging

from praxeon.sqlite_base import SqliteStore

logger = logging.getLogger(__name__)


def _is_expired(exp: datetime | None, now: datetime | None = None) -> bool:
    if not exp:
        return False
    current = now or datetime.now(UTC)
    if exp.tzinfo is None and current.tzinfo is not None:
        exp = exp.replace(tzinfo=UTC)
    elif exp.tzinfo is not None and current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current > exp


class NonceStore(ABC):
    """Interfaz abstracta para almacenes de nonces consumidos."""

    @abstractmethod
    def has_been_consumed(self, decision_id: str, nonce: str) -> bool:
        """Verifica si un par decision_id:nonce ya ha sido registrado como consumido."""
        pass

    @abstractmethod
    def consume(self, decision_id: str, nonce: str, expires_at: datetime | None = None) -> bool:
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
        self._store: dict[str, datetime | None] = {}

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

    def consume(self, decision_id: str, nonce: str, expires_at: datetime | None = None) -> bool:
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
        now = datetime.now(UTC)
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


class SqliteNonceStore(SqliteStore, NonceStore):
    """Almacén durable y transaccional basado en SQLite para prevención de replay attacks.
    
    Persiste capabilities y nonces consumidos en disco, sobreviviendo a reinicios
    del proceso y permitiendo sincronización concurrente entre múltiples procesos.
    """

    DEFAULT_DB_NAME = 'nonces.db'
    MIGRATIONS = (
        (   # versión 1: esquema histórico (IF NOT EXISTS: lo aceptan las bases creadas antes de las migraciones)
            """
            CREATE TABLE IF NOT EXISTS consumed_nonces (
                decision_id TEXT NOT NULL,
                nonce TEXT NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (decision_id, nonce)
            );
            """,
            'CREATE INDEX IF NOT EXISTS idx_expires_at ON consumed_nonces (expires_at);',
        ),
    )


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
                        logger.warning('Caducidad ilegible para el nonce de %s: se considera consumido', decision_id, exc_info=True)
                return True
            finally:
                self._close_conn(conn)

    def consume(self, decision_id: str, nonce: str, expires_at: datetime | None = None) -> bool:
        import sqlite3
        now = datetime.now(UTC)
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
        now_str = datetime.now(UTC).isoformat()
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
