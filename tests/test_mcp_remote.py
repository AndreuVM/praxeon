"""Servidores MCP remotos (SSE clásico y HTTP streamable): conexión, secretos y seguridad del transporte."""

import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from mcp_fake_http import FakeRemoteMcp  # noqa: E402

from praxeon.integrations.mcp_client import McpConfigStore, McpError, McpManager, McpServerConfig  # noqa: E402
from praxeon.integrations.mcp_client.client import SseMcpClient, StreamableHttpMcpClient, expand_vars, redact_url  # noqa: E402
from praxeon.policy.registry import ToolRegistry  # noqa: E402
from praxeon.server.app import create_app  # noqa: E402
from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service  # noqa: E402

TOKEN = "tok-secreto-123"


class FakeExecutor:
    def __init__(self):
        self.handlers = {}

    def register_handler(self, name, fn):
        self.handlers[name] = fn

    def unregister_handler(self, name):
        self.handlers.pop(name, None)


@pytest.fixture
def servers():
    made = []

    def make(**kw):
        s = FakeRemoteMcp(**kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


@pytest.fixture
def mcp(tmp_path):
    manager = McpManager(McpConfigStore(str(tmp_path / "mcp.json")), ToolRegistry(register_defaults=True), FakeExecutor())
    yield manager
    manager.close_all()


def add(mcp, **kw):
    mcp.store.save(McpServerConfig(name="remoto", **kw))


def approve(mcp):
    info = mcp.enable("remoto")
    assert info["status"] == "pending_approval", info
    return mcp.approve("remoto", info["tools_hash"])


# ---------------------------------------------------------------- SSE clásico

def test_sse_server_with_the_token_in_the_url_works_end_to_end(mcp, servers):
    srv = servers(mode="sse")
    add(mcp, url=f"{srv.base}/mcp/sse?token={TOKEN}")
    info = mcp.enable("remoto")
    assert info["status"] == "pending_approval" and {t["name"] for t in info["tools"]} == {"echo", "write_note", "boom"}
    mcp.approve("remoto", info["tools_hash"])

    out = mcp.executor.handlers["mcp__remoto__echo"]({"text": "hola remoto"})
    assert "hola remoto" in out and "NO instrucciones" in out
    with pytest.raises(McpError, match="explotó"):
        mcp.executor.handlers["mcp__remoto__boom"]({})

    get = next(r for r in srv.requests if r["method"] == "GET")
    assert get["query"]["token"] == [TOKEN]
    posts = [r for r in srv.requests if r["method"] == "POST"]
    assert posts and all(p["path"] == "/mcp/messages" and p["query"]["session_id"] == ["S1"] for p in posts)


def test_secrets_never_reach_the_api_description_or_the_errors(mcp, servers):
    srv = servers(mode="sse", token="otro-token-distinto")
    add(mcp, url=f"{srv.base}/mcp/sse?token={TOKEN}", headers={"X-Api-Key": "clave-header-9999"})
    info = mcp.enable("remoto")                 # el servidor rechaza el token → 401
    assert info["status"] == "error" and "401" in info["error"]
    blob = json.dumps(mcp.describe("remoto"), ensure_ascii=False)
    assert TOKEN not in blob and "clave-header-9999" not in blob
    assert "token=•••" in info["url"] and info["header_keys"] == ["X-Api-Key"] and info["kind"] == "remote"


def test_token_from_an_environment_variable_in_a_header(mcp, servers, monkeypatch):
    srv = servers(mode="sse")
    monkeypatch.setenv("PRAXEON_TEST_MCP_TOKEN", TOKEN)
    add(mcp, url=f"{srv.base}/mcp/sse", headers={"Authorization": "Bearer ${PRAXEON_TEST_MCP_TOKEN}"})
    approve(mcp)
    assert "ok" in mcp.executor.handlers["mcp__remoto__echo"]({"text": "ok"})
    assert next(r for r in srv.requests if r["method"] == "GET")["headers"]["authorization"] == f"Bearer {TOKEN}"
    assert "PRAXEON_TEST_MCP_TOKEN" in json.dumps(mcp.store.get("remoto").model_dump())   # en mcp.json queda la referencia, no el valor


def test_a_missing_environment_variable_is_reported_by_name_only(mcp, servers, monkeypatch):
    srv = servers(mode="sse")
    monkeypatch.delenv("PRAXEON_TEST_NO_EXISTE", raising=False)
    add(mcp, url=f"{srv.base}/mcp/sse?token=${{PRAXEON_TEST_NO_EXISTE}}")
    info = mcp.enable("remoto")
    assert info["status"] == "error" and "PRAXEON_TEST_NO_EXISTE" in info["error"] and not srv.requests


def test_an_sse_endpoint_from_another_origin_is_rejected(mcp, servers):
    srv = servers(mode="sse", endpoint_override="http://evil.example.com/steal")
    add(mcp, url=f"{srv.base}/mcp/sse?token={TOKEN}")
    info = mcp.enable("remoto")
    assert info["status"] == "error" and "otro origen" in info["error"]


def test_a_non_sse_response_is_an_explained_error(mcp, servers):
    srv = servers(mode="sse", content_type="text/html")
    add(mcp, url=f"{srv.base}/mcp/sse?token={TOKEN}")
    info = mcp.enable("remoto")
    assert info["status"] == "error" and "no es un flujo SSE" in info["error"]


def test_an_unreachable_server_reports_the_error_without_the_secret(mcp):
    add(mcp, url=f"http://127.0.0.1:9/mcp/sse?token={TOKEN}", timeout_s=3)
    info = mcp.enable("remoto")
    assert info["status"] == "error" and TOKEN not in json.dumps(info)


def test_a_dropped_sse_connection_is_reopened_on_the_next_call(mcp, servers):
    srv = servers(mode="sse")
    add(mcp, url=f"{srv.base}/mcp/sse?token={TOKEN}")
    approve(mcp)
    echo = mcp.executor.handlers["mcp__remoto__echo"]
    assert "uno" in echo({"text": "uno"})
    srv.drop_streams()
    assert "dos" in echo({"text": "dos"})
    assert len([r for r in srv.requests if r["method"] == "GET"]) == 2        # se abrió otra conexión


# ---------------------------------------------------------------- HTTP streamable

@pytest.mark.parametrize("mode", ["streamable", "streamable_sse"])
def test_streamable_http_server_json_or_sse_responses(mcp, servers, mode):
    srv = servers(mode=mode)
    add(mcp, url=f"{srv.base}/mcp?token={TOKEN}")
    approve(mcp)
    assert "hola" in mcp.executor.handlers["mcp__remoto__echo"]({"text": "hola"})
    later = [r for r in srv.requests if r["method"] == "POST" and r["body"].get("method") != "initialize"]
    assert later and all(r["headers"].get("mcp-session-id") == "SESION-1" for r in later)       # reutiliza la sesión
    assert all(r["headers"].get("mcp-protocol-version") for r in later)
    mcp.close_all()
    assert srv.deleted                                                                          # cierra la sesión al terminar


def test_auto_falls_back_to_sse_when_the_server_is_not_streamable(mcp, servers):
    srv = servers(mode="sse", sse_path="/mcp")          # URL sin «/sse» en un servidor SSE clásico
    add(mcp, url=f"{srv.base}/mcp?token={TOKEN}")
    approve(mcp)
    assert "ok" in mcp.executor.handlers["mcp__remoto__echo"]({"text": "ok"})
    methods = [(r["method"], r["path"]) for r in srv.requests]
    assert methods[0] == ("POST", "/mcp") and ("GET", "/mcp") in methods                       # probó streamable y retrocedió


def test_an_explicit_transport_is_not_second_guessed(mcp, servers):
    srv = servers(mode="sse", sse_path="/mcp")
    add(mcp, url=f"{srv.base}/mcp?token={TOKEN}", transport="streamable_http")
    assert mcp.enable("remoto")["status"] == "error"
    assert all(r["method"] == "POST" for r in srv.requests)                                    # no probó SSE


# ---------------------------------------------------------------- configuración y avisos

def test_config_needs_exactly_one_of_command_or_url():
    with pytest.raises(ValueError):
        McpServerConfig(name="a")
    with pytest.raises(ValueError):
        McpServerConfig(name="b", command="x", url="http://h/sse")
    with pytest.raises(ValueError):
        McpServerConfig(name="c", url="ftp://h/sse")
    with pytest.raises(ValueError):
        McpServerConfig(name="d", url="http:///sin-host")
    assert McpServerConfig(name="e", url="https://h/mcp").transport == "auto"


def test_changing_the_url_cancels_the_approval(mcp, servers):
    srv = servers(mode="sse")
    add(mcp, url=f"{srv.base}/mcp/sse?token={TOKEN}")
    approve(mcp)
    mcp.store.save(McpServerConfig(name="remoto", url=f"{srv.base}/mcp/sse?token=otro-token", enabled=True,
                                   approved_hash=mcp.store.get("remoto").approved_hash))
    saved = mcp.store.get("remoto")
    assert saved.approved_hash is None and saved.enabled is False


def test_warnings_for_cleartext_http_and_literal_tokens(mcp):
    add(mcp, url="http://34.66.33.205/mcp/sse?token=marq_live_secreto")
    warnings = mcp.describe("remoto")["warnings"]
    assert any("sin cifrar" in w for w in warnings) and any("token" in w and "${VARIABLE}" in w for w in warnings)

    mcp.store.save(McpServerConfig(name="seguro", url="https://api.ejemplo.com/mcp/sse?token=${MARQ_TOKEN}"))
    assert mcp.describe("seguro")["warnings"] == []                      # https + variable: sin avisos
    mcp.store.save(McpServerConfig(name="local", url="http://127.0.0.1:8000/sse?token=${X}"))
    assert mcp.describe("local")["warnings"] == []                       # loopback no necesita cifrado


def test_url_redaction_and_variable_expansion(monkeypatch):
    assert redact_url("https://u:pw@h.com:8443/mcp/sse?token=abc&x=${V}") == "https://h.com:8443/mcp/sse?token=•••&x=${V}"
    assert redact_url("http://h/mcp/AAAAAAAAAAAAAAAAAAAAAAAAAAAA/sse") == "http://h/mcp/•••/sse"
    monkeypatch.setenv("PRAXEON_T1", "valor")
    assert expand_vars("a=${PRAXEON_T1}&b=${PRAXEON_T1}") == "a=valor&b=valor"
    with pytest.raises(McpError, match="PRAXEON_NOPE"):
        expand_vars("${PRAXEON_NOPE}", "la URL")


def test_clients_reject_non_http_urls():
    for cls in (SseMcpClient, StreamableHttpMcpClient):
        with pytest.raises(McpError):
            cls("file:///etc/passwd")


# ---------------------------------------------------------------- API

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXEON_CACHE_DIR", str(tmp_path / "cache"))
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    yield TestClient(create_app(profile="dev")), service
    service.mcp.close_all()
    set_runtime_service(None)


def test_api_adds_a_remote_server_hides_the_url_and_keeps_it_when_editing(client, servers):
    api, service = client
    srv = servers(mode="sse")
    body = {"url": f"{srv.base}/mcp/sse?token={TOKEN}", "headers": {"X-Extra": "valor-cabecera"}}
    created = api.put("/v1/mcp/servers/remoto", json=body).json()["data"]
    assert created["kind"] == "remote" and created["command"] is None and TOKEN not in json.dumps(created)
    assert "valor-cabecera" not in api.get("/v1/mcp/servers").text

    enabled = api.post("/v1/mcp/servers/remoto/enable").json()["data"]
    assert enabled["status"] == "pending_approval"
    api.post("/v1/mcp/servers/remoto/approve", json={"tools_hash": enabled["tools_hash"]})

    # editar solo un ajuste: la URL (que la API no devuelve) y la cabecera se conservan sin reenviarlas
    edited = api.put("/v1/mcp/servers/remoto", json={"url": "", "headers": {"X-Extra": None}, "trust_hints": True}).json()["data"]
    stored = service.mcp.store.get("remoto")
    assert stored.url == body["url"] and stored.headers == {"X-Extra": "valor-cabecera"} and stored.trust_hints
    assert edited["status"] == "running"                          # nada cambió en lo que se conecta: sigue aprobado

    assert api.put("/v1/mcp/servers/otro", json={"headers": {"X": None}, "url": "http://h/sse"}).status_code == 422
    assert api.put("/v1/mcp/servers/nada", json={}).status_code == 422        # ni comando ni url
    assert api.put("/v1/mcp/servers/ambos", json={"command": "x", "url": "http://h/sse"}).status_code == 422


def test_the_remote_server_works_inside_a_real_mission(tmp_path, monkeypatch, servers):
    """El agente llama a una herramienta remota de solo lectura de confianza a través del supervisor."""
    from unittest.mock import patch

    from praxeon.agent_llm import BaseAgentLLM

    class Scripted(BaseAgentLLM):
        provider_name, model_name = "scripted", "s-1"

        def __init__(self):
            self.turns = ['Thought: pruebo\nAction: mcp__remoto__echo({"text": "desde la misión"})',
                          'Thought: fin\nAction: finish({"summary": "Resultado entregado tras usar la herramienta remota."})']
            self.calls = []

        def generate(self, messages, system_prompt=None):
            self.calls.append(json.dumps(messages, ensure_ascii=False))
            return self.turns.pop(0)

    monkeypatch.setenv("PRAXEON_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_SECRET_KEY", raising=False)
    srv = servers(mode="streamable")
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    try:
        service.mcp.store.save(McpServerConfig(name="remoto", url=f"{srv.base}/mcp?token={TOKEN}", trust_hints=True))
        info = service.mcp.enable("remoto")
        service.mcp.approve("remoto", info["tools_hash"])
        llm = Scripted()
        with patch("praxeon.agent_llm.create_agent_llm", return_value=llm):
            meta = service.start_mission(goal="usa la herramienta remota", session_id="remote-m1", llm_provider="scripted",
                                         workspace_root=str(tmp_path), step_delay_ms=0)
        deadline = time.time() + 90
        while time.time() < deadline and not any(e.type.value == "session.completed" for e in service.event_bus.get_events(meta["session_id"], limit=2000)):
            time.sleep(0.2)
        assert "desde la misión" in llm.calls[1] and "NO instrucciones" in llm.calls[1]
    finally:
        service.mcp.close_all()
        set_runtime_service(None)


# ---------------------------------------------------------------- interoperabilidad con el SDK oficial

@pytest.mark.parametrize("kind, path", [("sse", "/sse"), ("streamable", "/mcp")])
def test_interoperates_with_remote_servers_built_with_the_official_sdk(mcp, kind, path):
    import socket
    import subprocess

    pytest.importorskip("mcp")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "mcp_sdk_remote_server.py"), kind, str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
                break
            except OSError:
                time.sleep(0.2)
        add(mcp, url=f"http://127.0.0.1:{port}{path}", timeout_s=30)      # transporte «auto»
        info = mcp.enable("remoto")
        assert info["status"] == "pending_approval", info
        assert {t["name"] for t in info["tools"]} == {"add", "shout"}
        mcp.approve("remoto", info["tools_hash"])
        assert "5" in mcp.executor.handlers["mcp__remoto__add"]({"a": 2, "b": 3})
        assert "HOLA" in mcp.executor.handlers["mcp__remoto__shout"]({"text": "hola"})
    finally:
        mcp.close_all()
        proc.kill()
        proc.wait(timeout=5)
