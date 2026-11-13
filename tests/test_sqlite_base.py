"""Base común de los almacenes SQLite: WAL una vez y migraciones con PRAGMA user_version (auditoría A-04/P-01)."""

import sqlite3

import pytest

from praxeon.runtime.decision_store import SqliteDecisionRepository
from praxeon.runtime.event_bus import EventStore
from praxeon.runtime.nonce_store import SqliteNonceStore
from praxeon.runtime.state_store import SqliteStateStore
from praxeon.persistence.sqlite_store import SqlitePersistenceStore
from praxeon.server.services.auth_service import RevocationStore
from praxeon.sqlite_base import SqliteStore


class _Notes(SqliteStore):
    DEFAULT_DB_NAME = "notes.db"
    MIGRATIONS = (
        ("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)",),
        ("ALTER TABLE notes ADD COLUMN author TEXT NOT NULL DEFAULT ''",),
    )


def _version(path) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def test_migrations_run_in_order_and_record_the_version(tmp_path):
    db = tmp_path / "n.db"
    store = _Notes(str(db))
    assert store.schema_version == 2 == _version(db)
    with sqlite3.connect(db) as conn:
        assert {r[1] for r in conn.execute("PRAGMA table_info(notes)")} == {"id", "text", "author"}
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    _Notes(str(db))                      # reabrir no vuelve a migrar (el ALTER fallaría si lo hiciera)
    assert _version(db) == 2


def test_a_database_created_before_migrations_existed_is_adopted_without_losing_data(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:     # como la dejaba la versión anterior: tabla creada y user_version 0
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)")
        conn.execute("INSERT INTO notes (text) VALUES ('antigua')")
    _Notes(str(db))
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT text, author FROM notes").fetchall() == [("antigua", "")]
    assert _version(db) == 2


def test_a_failing_migration_leaves_the_previous_version(tmp_path):
    class _Broken(SqliteStore):
        DEFAULT_DB_NAME = "b.db"
        MIGRATIONS = (
            ("CREATE TABLE IF NOT EXISTS t (x INTEGER)",),
            ("CREATE TABLE u (y INTEGER)", "ESTO NO ES SQL"),
        )

    db = tmp_path / "b.db"
    with pytest.raises(sqlite3.Error):
        _Broken(str(db))
    assert _version(db) == 1
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "u" not in tables              # la migración 2 se deshizo entera


def test_a_database_from_a_newer_version_is_left_untouched(tmp_path, caplog):
    db = tmp_path / "future.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 9")
    with caplog.at_level("WARNING"):
        _Notes(str(db))
    assert _version(db) == 9 and "versión de esquema 9" in caplog.text


@pytest.mark.parametrize("store_cls", [
    SqliteNonceStore, EventStore, SqliteDecisionRepository, SqliteStateStore, SqlitePersistenceStore, RevocationStore,
])
def test_every_runtime_store_is_versioned(store_cls, tmp_path):
    db = tmp_path / "s.db"
    store = store_cls(str(db))
    assert store.schema_version == len(store_cls.MIGRATIONS) >= 1
