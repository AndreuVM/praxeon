"""Optimización de llamadas a API: prefijo estable, uso real de tokens, caché de Anthropic y contexto de Ollama."""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from praxeon.agent_llm import AnthropicLLM, ClaudeCLILLM, OllamaLLM, OpenAICompatibleLLM, create_agent_llm
from praxeon.context.manager import ContextManager


def _steps(n):
    return [
        {"step": i + 1, "thought": f"t{i}", "tool": "read_file", "arguments": {"path": f"f{i}.py"}, "observation": "x" * 400, "verdict": "ALLOW"}
        for i in range(n)
    ]


def _common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


# ---------------------------------------------------------------- ventana de contexto

def test_default_compression_slides_every_step_and_breaks_the_prefix():
    cm = ContextManager()
    prev = cm.assemble_mission_prompt(goal="g", steps_history=_steps(5), node_mode=True)
    now = cm.assemble_mission_prompt(goal="g", steps_history=_steps(6), node_mode=True)
    assert _common_prefix(prev, now) == 1                       # solo el mensaje raíz


def test_batch_compression_keeps_the_conversation_append_only_between_batches():
    cm = ContextManager()
    resets = 0
    prev = None
    for n in range(1, 31):
        conv = cm.assemble_mission_prompt(goal="g", steps_history=_steps(n), node_mode=True, compress_batch=6)
        if prev is not None and _common_prefix(prev, conv) < len(prev):
            resets += 1
        prev = conv
    assert resets <= 30 // 6                                    # el prefijo solo cambia al avanzar de lote (antes: cada paso)
    # nunca se pierde la memoria de los pasos antiguos: o están en el resumen o están completos
    text = json.dumps(prev)
    assert "f0.py" in text and "f29.py" in text and len(prev) <= 1 + 1 + 2 * (4 + 5)


def test_recent_steps_are_always_kept_in_full_with_batching():
    conv = ContextManager().assemble_mission_prompt(goal="g", steps_history=_steps(9), node_mode=True, compress_batch=6)
    assert sum(1 for m in conv if m["role"] == "assistant") >= 4


def test_no_compression_keeps_every_step_so_a_stateful_session_can_continue():
    cm = ContextManager()
    prev = cm.assemble_mission_prompt(goal="g", steps_history=_steps(9), node_mode=True, compress=False)
    now = cm.assemble_mission_prompt(goal="g", steps_history=_steps(10), node_mode=True, compress=False)
    assert _common_prefix(prev, now) == len(prev) and len(now) == 1 + 2 * 10

    # y la CLI de Claude puede seguir con el mismo proceso en cada turno
    session = type("S", (), {"n_turns": len(prev), "users": [m["content"] for m in prev if m["role"] == "user"]})
    assert ClaudeCLILLM._continuation(session, [(m["role"], m["content"]) for m in now]) is not None
    assert ClaudeCLILLM.stateful_session is True


# ---------------------------------------------------------------- servidores falsos

class FakeServer:
    def __init__(self, routes):
        self.routes = routes     # path -> (status, body)
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                outer.requests.append({"path": self.path, "body": body})
                status, payload = outer.routes.get(self.path, (404, {"error": "no"}))
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake_server():
    made = []

    def make(routes):
        s = FakeServer(routes)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


# ---------------------------------------------------------------- uso real de tokens

def test_openai_compatible_reports_real_and_cached_tokens(fake_server):
    srv = fake_server({"/v1/chat/completions": (200, {
        "choices": [{"message": {"content": "hola"}}],
        "usage": {"prompt_tokens": 2000, "completion_tokens": 30, "prompt_tokens_details": {"cached_tokens": 1536}},
    })})
    llm = OpenAICompatibleLLM(base_url=srv.url, model="m", provider_name="openai")
    assert llm.generate([{"role": "user", "content": "x"}]) == "hola"
    assert llm.last_usage == {"prompt_tokens": 2000, "output_tokens": 30, "cached_tokens": 1536}


def test_anthropic_enables_prompt_caching_only_on_the_official_api_and_reports_usage(fake_server):
    assert AnthropicLLM(api_key="k")._prompt_cache is True
    assert AnthropicLLM(api_key="k", base_url="https://proxy.ejemplo.com")._prompt_cache is False

    srv = fake_server({"/v1/messages": (200, {
        "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 12, "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 200, "output_tokens": 40},
    })})
    llm = AnthropicLLM(api_key="k", base_url=srv.url, prompt_cache=True)
    assert llm.generate([{"role": "user", "content": "x"}], system_prompt="s") == "ok"
    assert srv.requests[0]["body"]["cache_control"] == {"type": "ephemeral"}
    assert llm.last_usage == {"prompt_tokens": 3212, "output_tokens": 40, "cached_tokens": 3000}

    srv2 = fake_server({"/v1/messages": (200, {"content": [{"type": "text", "text": "ok"}]})})
    AnthropicLLM(api_key="k", base_url=srv2.url).generate([{"role": "user", "content": "x"}])
    assert "cache_control" not in srv2.requests[0]["body"]      # endpoints personalizados no reciben campos propios


# ---------------------------------------------------------------- Ollama

def test_ollama_uses_the_native_api_with_a_fixed_num_ctx(fake_server, monkeypatch):
    monkeypatch.delenv("PRAXEON_OLLAMA_NUM_CTX", raising=False)
    srv = fake_server({"/api/chat": (200, {"message": {"content": " hecho "}, "prompt_eval_count": 1200, "eval_count": 20})})
    llm = OllamaLLM(base_url=srv.url, model="qwen2.5-coder:7b", provider_name="ollama", max_tokens=777)
    assert isinstance(llm, OpenAICompatibleLLM) and llm.num_ctx == 8192
    assert llm.generate([{"role": "user", "content": "hola"}], system_prompt="sys") == "hecho"
    body = srv.requests[0]["body"]
    assert srv.requests[0]["path"] == "/api/chat" and body["stream"] is False
    assert body["options"]["num_ctx"] == 8192 and body["options"]["num_predict"] == 777
    assert body["messages"][0] == {"role": "system", "content": "sys"} and body["keep_alive"]
    assert llm.last_usage == {"prompt_tokens": 1200, "output_tokens": 20, "cached_tokens": 0}


def test_ollama_context_size_is_configurable_and_a_full_context_is_reported(fake_server, monkeypatch, caplog):
    monkeypatch.setenv("PRAXEON_OLLAMA_NUM_CTX", "4096")
    srv = fake_server({"/api/chat": (200, {"message": {"content": "x"}, "prompt_eval_count": 4090, "eval_count": 5})})
    llm = OllamaLLM(base_url=srv.url, model="m", provider_name="ollama")
    assert llm.num_ctx == 4096
    with caplog.at_level(logging.WARNING, logger="praxeon.agent_llm"):
        llm.generate([{"role": "user", "content": "hola"}])
    assert any("num_ctx=4096" in r.message for r in caplog.records)


def test_ollama_falls_back_to_the_openai_endpoint_when_the_native_api_is_missing(fake_server):
    srv = fake_server({"/v1/chat/completions": (200, {"choices": [{"message": {"content": "via v1"}}]})})
    llm = OllamaLLM(base_url=srv.url, model="m", provider_name="ollama")
    assert llm.generate([{"role": "user", "content": "x"}]) == "via v1"
    assert llm.generate([{"role": "user", "content": "y"}]) == "via v1"
    assert [r["path"] for r in srv.requests] == ["/api/chat", "/v1/chat/completions", "/v1/chat/completions"]   # tras el 404 no insiste


def test_factory_builds_the_native_ollama_client():
    with patch("praxeon.agent_llm.is_ollama_online", return_value=True):
        llm = create_agent_llm(provider="ollama", model="qwen2.5-coder:7b")
    assert isinstance(llm, OllamaLLM) and llm.provider_name == "ollama"


# ---------------------------------------------------------------- runtime

def test_runtime_records_real_tokens_reported_by_the_provider(tmp_path, monkeypatch):
    from praxeon.agent_llm import BaseAgentLLM
    from praxeon.server.dependencies import RuntimeApplicationService, set_runtime_service

    class UsageLLM(BaseAgentLLM):
        provider_name = "usage"
        model_name = "usage-1"

        def generate(self, messages, system_prompt=None):
            self._record_usage(1000, 50, 900)
            return 'Thought: listo\nAction: finish({"summary": "hecho"})'

    monkeypatch.delenv("PRAXEON_API_KEY", raising=False)
    monkeypatch.delenv("PRAXEON_SECRET_KEY", raising=False)
    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    set_runtime_service(service)
    try:
        with patch("praxeon.agent_llm.create_agent_llm", return_value=UsageLLM()):
            meta = service.start_mission(goal="di hola", session_id="cache-m1", llm_provider="usage", workspace_root=str(tmp_path), step_delay_ms=0)
            sid = meta["session_id"]
            deadline = time.time() + 60
            while time.time() < deadline and service._running_missions.get(sid, {}).get("status") is None:
                time.sleep(0.1)
        metrics = service.context_manager.get_metrics()
        assert metrics["actual_prompt_tokens"] >= 1000 and metrics["actual_cached_tokens"] >= 900
        assert metrics["cache_hit_status"] == "hit"
    finally:
        set_runtime_service(None)
