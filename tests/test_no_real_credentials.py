"""La suite no usa las claves reales del desarrollador: ni carga el .env ni hereda claves de proveedores (AUD-25)."""

import os

import pytest

from praxeon.config import PraxeonConfig
from tests.conftest import PROVIDER_CREDENTIALS


@pytest.mark.parametrize("name", PROVIDER_CREDENTIALS)
def test_no_provider_credential_reaches_the_tests(name):
    assert name not in os.environ


def test_the_env_file_is_not_loaded_while_testing(monkeypatch):
    calls = []
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: calls.append(1))
    PraxeonConfig.from_env(load_env_file=True)
    assert calls == []


def test_outside_the_tests_the_env_file_is_still_loaded(monkeypatch):
    calls = []
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: calls.append(1))
    monkeypatch.delenv("PRAXEON_NO_DOTENV")
    PraxeonConfig.from_env(load_env_file=True)
    assert calls == [1]
