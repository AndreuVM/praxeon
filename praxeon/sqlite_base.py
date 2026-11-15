"""Base común de los almacenes SQLite de PRAXEON (praxeon/sqlite_base.py).

Antes cada almacén abría una conexión nueva por operación, repetía `PRAGMA journal_mode=WAL` cada vez y creaba su esquema
con `CREATE TABLE IF NOT EXISTS`, sin versión (auditoría A-04/P-01). Esta base:

- activa WAL una sola vez, al abrir el fichero (es una propiedad persistente de la base de datos), no en cada conexión;
- abre una conexión por operación y la cierra al terminar. Reutilizar conexiones por hilo bloquea el fichero en Windows
  mientras el proceso vive (tests y borrado de la caché fallan): se hará cuando el ciclo de vida de los almacenes lo
  gestione la raíz de composición (AUD-24);
- aplica migraciones ordenadas y guarda la versión en `PRAGMA user_version`, dentro de la misma transacción que la migración.

Uso:

    class MiAlmacen(SqliteStore):
        DEFAULT_DB_NAME = "mi_almacen.db"
        MIGRATIONS = (
            ("CREATE TABLE IF NOT EXISTS t (...)", "CREATE INDEX IF NOT EXISTS ..."),   # versión 1
            (lambda conn: ...,),                                                       # versión 2
        )

La migración 1 de cada almacén es su esquema histórico con `IF NOT EXISTS`: una base de datos creada por una versión anterior
(user_version 0, tablas ya presentes) la acepta sin cambios y queda en la versión 1.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import ClassVar
from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)

MigrationStep = str | Callable[[sqlite3.Connection], None]
Migration = Sequence[MigrationStep]

MEMORY = ":memory:"


class SqliteStore:
    """WAL una vez, conexión por operación y migraciones con `PRAGMA user_version`."""

    DEFAULT_DB_NAME: ClassVar[str] = "praxeon.db"
    MIGRATIONS: ClassVar[tuple[Migration, ...]] = ()
    BUSY_TIMEOUT_SECONDS: ClassVar[float] = 30.0
    ROW_FACTORY: ClassVar[Callable[..., object] | None] = None   # p. ej. sqlite3.Row

    def __init__(self, db_path: str | None = None) -> None:
        if db_path == MEMORY:
            self.db_path = MEMORY
        else:
            from praxeon.config import resolve_db_path

            self.db_path = str(resolve_db_path(self.DEFAULT_DB_NAME, db_path))
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._mem_conn: sqlite3.Connection | None = None
        self._migrate()

    # ------------------------------------------------------------------ conexiones
    def _open(self) -> sqlite3.Connection:
        if self.db_path == MEMORY:
            # Una base :memory: es privada de su conexión: se comparte una sola entre hilos, protegida por self._lock
            conn = sqlite3.connect(MEMORY, check_same_thread=False)
        else:
            conn = sqlite3.connect(self.db_path, timeout=self.BUSY_TIMEOUT_SECONDS)
            conn.execute("PRAGMA synchronous=NORMAL;")      # ajuste por conexión
        if self.ROW_FACTORY is not None:
            conn.row_factory = self.ROW_FACTORY
        return conn

    def _get_connection(self) -> sqlite3.Connection:
        """Conexión para una operación (la única y compartida si la base es :memory:). Ciérrala con `_close_conn`."""
        if self.db_path == MEMORY:
            if self._mem_conn is None:
                self._mem_conn = self._open()
            return self._mem_conn
        return self._open()

    def _close_conn(self, conn: sqlite3.Connection) -> None:
        if conn is not self._mem_conn:
            conn.close()

    def close(self) -> None:
        """Cierra la conexión de una base :memory: (las de fichero ya se cierran tras cada operación)."""
        if self._mem_conn is not None:
            self._mem_conn.close()
            self._mem_conn = None

    # ------------------------------------------------------------------ esquema
    @property
    def schema_version(self) -> int:
        conn = self._get_connection()
        try:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            self._close_conn(conn)

    def _migrate(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                self._apply_migrations(conn)
            finally:
                self._close_conn(conn)

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        if self.db_path != MEMORY:
            conn.execute("PRAGMA journal_mode=WAL;")     # persistente en el fichero: basta con hacerlo al abrirlo
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        target = len(self.MIGRATIONS)
        if current > target:
            logger.warning(
                "%s: la base de datos %s está en la versión de esquema %d y este código conoce hasta la %d",
                type(self).__name__, self.db_path, current, target,
            )
            return
        for version in range(current + 1, target + 1):
            # Transacción explícita: `sqlite3` no abre una antes de DDL (CREATE/ALTER), así que `with conn:` no bastaría
            # para deshacer una migración a medias. La migración y su número de versión se aplican juntos o nada.
            conn.execute("BEGIN IMMEDIATE")
            try:
                for step in self.MIGRATIONS[version - 1]:
                    if callable(step):
                        step(conn)                        # no debe hacer commit: lo hace la base al final
                    else:
                        conn.execute(step)
                conn.execute(f"PRAGMA user_version = {version}")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            if version > 1 or current > 0:
                logger.info("%s: esquema migrado a la versión %d (%s)", type(self).__name__, version, self.db_path)
