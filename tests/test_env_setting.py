"""Variables de entorno PRAXEON_* con compatibilidad JEV_* (auditoría A-08)."""

import warnings

import pytest

from praxeon.config import PraxeonConfig
from praxeon.env import env_setting


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("CHUNK_SIZE", "API_KEY", "MAX_RETRIES"):
        monkeypatch.delenv(f"PRAXEON_{name}", raising=False)
        monkeypatch.delenv(f"JEV_{name}", raising=False)


def test_the_praxeon_name_wins_and_does_not_warn(monkeypatch):
    monkeypatch.setenv("PRAXEON_CHUNK_SIZE", "7")
    monkeypatch.setenv("JEV_CHUNK_SIZE", "9")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert env_setting("CHUNK_SIZE", "3") == "7"


def test_the_legacy_name_still_works_with_a_deprecation_warning(monkeypatch):
    monkeypatch.setenv("JEV_CHUNK_SIZE", "9")
    with pytest.warns(DeprecationWarning, match="PRAXEON_CHUNK_SIZE"):
        assert env_setting("CHUNK_SIZE", "3") == "9"


def test_the_default_applies_when_neither_is_set():
    assert env_setting("CHUNK_SIZE", "3") == "3"


def test_config_reads_the_new_names(monkeypatch):
    monkeypatch.setenv("PRAXEON_CHUNK_SIZE", "5")
    monkeypatch.setenv("PRAXEON_MAX_RETRIES", "4")
    cfg = PraxeonConfig.from_env()
    assert cfg.chunk.chunk_size == 5 and cfg.retry.max_retries == 4


def test_a_weak_legacy_api_key_is_rejected_in_production(monkeypatch):
    # Antes, el perfil de producción solo miraba PRAXEON_API_KEY, pero auth_service aceptaba JEV_API_KEY:
    # una clave débil con el nombre antiguo pasaba la comprobación.
    from praxeon.server.app import validate_security_profile

    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.setenv("PRAXEON_SECRET_KEY", "m" * 40)
    monkeypatch.setenv("PRAXEON_CORS_ORIGINS", "https://praxeon.example.com")
    monkeypatch.setenv("JEV_API_KEY", "corta")
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="16 caracteres"):
        validate_security_profile("production")
