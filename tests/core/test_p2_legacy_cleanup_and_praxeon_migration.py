"""Tests de validación para la migración de nombres legados y resolución de caché (Task 27)."""

import os
from pathlib import Path
import pytest

from praxeon.config import resolve_cache_dir, resolve_db_path
from praxeon.core import JEVEngine, PraxeonEngine
from praxeon.dashboard import JEVDashboard, PraxeonDashboard


def test_praxeon_engine_and_dashboard_aliases():
    assert PraxeonEngine is JEVEngine
    assert PraxeonDashboard is JEVDashboard


def test_resolve_cache_dir_defaults_to_praxeon_cache(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PRAXEON_CACHE_DIR", raising=False)
    monkeypatch.delenv("JEV_CACHE_DIR", raising=False)

    resolved = resolve_cache_dir()
    assert ".praxeon_cache" in resolved


def test_resolve_cache_dir_transparent_fallback(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PRAXEON_CACHE_DIR", raising=False)
    monkeypatch.delenv("JEV_CACHE_DIR", raising=False)

    legacy_dir = tmp_path / ".jev_cache"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "state.db").write_text("dummy state")

    resolved = resolve_cache_dir()
    assert ".jev_cache" in resolved


def test_resolve_cache_dir_env_override(monkeypatch, tmp_path):
    custom_dir = str(tmp_path / "custom_praxeon_data")
    monkeypatch.setenv("PRAXEON_CACHE_DIR", custom_dir)

    resolved = resolve_cache_dir()
    assert resolved == custom_dir


def test_resolve_db_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PRAXEON_CACHE_DIR", str(tmp_path / ".praxeon_cache"))

    db_path = resolve_db_path("test.db")
    assert db_path.endswith("test.db")
    assert os.path.isdir(os.path.dirname(db_path))
