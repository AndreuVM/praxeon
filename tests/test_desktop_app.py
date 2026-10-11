"""Aplicación de escritorio: servidor local autenticado con token efímero."""

import json
import urllib.error
import urllib.request

import pytest

from praxeon.desktop.app import LOOPBACK, find_free_port, frontend_available, start_local_server


def _get(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("X-API-Key", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


@pytest.fixture
def local_server(monkeypatch):
    for key in ("PRAXEON_API_KEY", "PRAXEON_SECRET_KEY", "PRAXEON_CORS_ORIGINS", "PRAXEON_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    server = start_local_server()
    yield server
    server.stop()


def test_find_free_port_is_usable():
    assert 1024 <= find_free_port() <= 65535


def test_local_server_binds_loopback_and_requires_token(local_server):
    assert local_server.host == LOOPBACK == "127.0.0.1"
    status, _ = _get(f"{local_server.base_url}/v1/sessions")
    assert status == 401
    status, body = _get(f"{local_server.base_url}/v1/sessions", token=local_server.token)
    assert status == 200 and json.loads(body)["success"] is True


def test_wrong_token_is_rejected(local_server):
    status, _ = _get(f"{local_server.base_url}/v1/sessions", token=local_server.token + "x")
    assert status == 401


def test_token_travels_in_fragment_only(local_server):
    assert "#token=" in local_server.window_url
    assert local_server.token not in local_server.base_url


def test_tokens_differ_between_launches(monkeypatch):
    for key in ("PRAXEON_API_KEY", "PRAXEON_SECRET_KEY", "PRAXEON_CORS_ORIGINS"):
        monkeypatch.delenv(key, raising=False)
    a = start_local_server()
    a.stop()
    b = start_local_server()
    b.stop()
    assert a.token != b.token


def test_frontend_is_packaged():
    assert frontend_available()


def test_stop_restores_environment(monkeypatch):
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.setenv("PRAXEON_SECRET_KEY", "previo")
    import os
    server = start_local_server()
    assert os.environ["PRAXEON_API_KEY"] == server.token
    server.stop()
    assert "PRAXEON_API_KEY" not in os.environ
    assert os.environ["PRAXEON_SECRET_KEY"] == "previo"
