"""Un único acceso al SDK de TypeSafe para el proveedor del runtime y el cliente del DecisionEngine (auditoría A-02)."""

from unittest.mock import MagicMock

import pytest

from praxeon.config import PraxeonConfig
from praxeon.core import typesafe_client as legacy
from praxeon.providers import typesafe as provider


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(provider.time, "sleep", lambda s: None)


def _client(*effects):
    client = MagicMock()
    client.system_one.side_effect = list(effects)
    return client


def test_transient_failures_are_retried():
    client = _client(RuntimeError("503 Service Unavailable"), RuntimeError("connection reset"), "ok")
    assert provider.invoke_system_one(client, {}, {}, "jev-v1", max_retries=2) == "ok"
    assert client.system_one.call_count == 3


def test_other_failures_are_not_retried():
    client = _client(ValueError("invalid question schema"), "ok")
    with pytest.raises(ValueError):
        provider.invoke_system_one(client, {}, {}, "jev-v1", max_retries=2)
    assert client.system_one.call_count == 1


def test_the_circuit_breaker_sees_every_attempt():
    breaker = MagicMock()
    provider.invoke_system_one(_client(RuntimeError("timeout"), "ok"), {}, {}, "jev-v1", circuit_breaker=breaker)
    assert breaker.record_failure.call_count == 1 and breaker.record_success.call_count == 1


def test_no_key_means_no_client():
    assert provider.create_sdk_client(None) is None and provider.create_sdk_client("") is None


def test_the_decision_engine_client_uses_the_shared_access(monkeypatch):
    calls = []
    monkeypatch.setattr(legacy, "invoke_system_one", lambda *args, **kw: calls.append(args) or "resp")
    client = legacy.TypeSafeJEVClient(PraxeonConfig(typesafe_api_key="k", use_typesafe_api=True))
    client._client = MagicMock()
    assert client._invoke_system_one_with_retry({"s": 1}, {"q": 2}) == "resp"
    assert calls == [(client._client, {"s": 1}, {"q": 2}, client.config.typesafe_model, 2)]
    assert legacy.Choice is provider.Choice and legacy.Noul is provider.Noul
