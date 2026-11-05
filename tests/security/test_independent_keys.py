"""Una clave por propósito: API, JWT y recibos son independientes (auditoría S-03)."""

import pytest

from praxeon.runtime import keys
from praxeon.server.app import validate_network_binding
from praxeon.server.services.auth_service import AuthService

MASTER = "m" * 40
VARS = ("PRAXEON_API_KEY", "PRAXEON_SECRET_KEY", "PRAXEON_JWT_SECRET", "PRAXEON_RECEIPT_KEY", "PRAXEON_PROFILE", "PRAXEON_ENV", "JEV_API_KEY")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in VARS:
        monkeypatch.delenv(name, raising=False)


def test_subkeys_derived_from_one_master_are_distinct_and_stable(monkeypatch):
    monkeypatch.setenv("PRAXEON_SECRET_KEY", MASTER)
    jwt, receipt = keys.jwt_secret(), keys.receipt_key()
    assert jwt and receipt and jwt != receipt and MASTER not in (jwt, receipt)
    assert (jwt, receipt) == (keys.jwt_secret(), keys.receipt_key())        # estables entre reinicios
    assert keys.derive_key(MASTER, "jwt") != keys.derive_key(MASTER + "x", "jwt")


def test_explicit_subkeys_win_over_the_derived_ones(monkeypatch):
    monkeypatch.setenv("PRAXEON_SECRET_KEY", MASTER)
    monkeypatch.setenv("PRAXEON_JWT_SECRET", "j" * 40)
    monkeypatch.setenv("PRAXEON_RECEIPT_KEY", "r" * 40)
    assert keys.jwt_secret() == "j" * 40 and keys.receipt_key() == "r" * 40


def test_without_any_key_there_is_nothing_to_share():
    assert keys.jwt_secret() is None and keys.receipt_key() is None


def test_the_master_secret_is_not_accepted_as_an_api_key(monkeypatch):
    from fastapi import HTTPException

    from praxeon.server.dependencies import verify_api_key
    monkeypatch.setenv("PRAXEON_SECRET_KEY", MASTER)           # sin PRAXEON_API_KEY
    monkeypatch.setenv("PRAXEON_REQUIRE_AUTH", "1")
    with pytest.raises(HTTPException) as err:
        verify_api_key(request=None, x_api_key=MASTER, authorization=None)
    assert err.value.status_code in (401, 500)


def test_a_jwt_signed_with_the_receipt_key_is_not_valid(monkeypatch):
    from praxeon.server.services.auth_service import decode_jwt_hs256

    monkeypatch.setenv("PRAXEON_SECRET_KEY", MASTER)
    svc = AuthService(api_key="a" * 20)
    token = svc.issue_token_pair().access_token
    assert decode_jwt_hs256(token, keys.jwt_secret())                       # lo firma la clave de JWT…
    with pytest.raises(Exception):
        decode_jwt_hs256(token, keys.receipt_key())                         # …y la de recibos no lo valida
    with pytest.raises(Exception):
        decode_jwt_hs256(token, MASTER)                                     # ni el secreto maestro


def test_network_binding_needs_an_api_key_not_just_the_master_secret(monkeypatch):
    monkeypatch.setenv("PRAXEON_SECRET_KEY", MASTER)
    with pytest.raises(ValueError, match="PRAXEON_API_KEY"):
        validate_network_binding("0.0.0.0")
    monkeypatch.setenv("PRAXEON_API_KEY", "k" * 20)
    monkeypatch.setenv("PRAXEON_ALLOWED_HOSTS", "praxeon.midominio.test")
    validate_network_binding("0.0.0.0")


def test_production_accepts_a_master_secret_or_both_explicit_subkeys(monkeypatch):
    assert keys.production_keys_problem()
    monkeypatch.setenv("PRAXEON_SECRET_KEY", MASTER)
    assert keys.production_keys_problem() is None
    monkeypatch.delenv("PRAXEON_SECRET_KEY")
    monkeypatch.setenv("PRAXEON_JWT_SECRET", "j" * 40)
    assert keys.production_keys_problem()                      # falta la de recibos
    monkeypatch.setenv("PRAXEON_RECEIPT_KEY", "r" * 40)
    assert keys.production_keys_problem() is None
    monkeypatch.setenv("PRAXEON_RECEIPT_KEY", "default")
    assert keys.production_keys_problem()
