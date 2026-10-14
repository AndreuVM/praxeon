"""Proveedor Anthropic (Claude por API): formato de la petición, reintentos, errores y reglas de la fábrica."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from praxeon.agent_llm import AnthropicLLM, SimulatedAgentLLM, create_agent_llm


class FakeAnthropic:
    """Servidor local que imita POST /v1/messages y registra lo recibido."""

    def __init__(self, responses):
        self.responses = list(responses)  # [(status, body_dict, headers)]
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": json.loads(self.rfile.read(length))})
                status, body, headers = outer.responses.pop(0) if outer.responses else (200, {"content": [{"type": "text", "text": "ok"}]}, {})
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake():
    servers = []

    def make(responses):
        s = FakeAnthropic(responses)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


def ok(text):
    return (200, {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}, {})


def test_request_has_the_anthropic_format_and_the_text_is_returned(fake):
    srv = fake([(200, {"content": [{"type": "text", "text": "Thought: x\n"}, {"type": "text", "text": "Action: finish(\"ok\")"}]}, {})])
    llm = AnthropicLLM(api_key="sk-ant-test", model="claude-sonnet-5-5", base_url=srv.url)
    out = llm.generate(
        [{"role": "assistant", "content": "turno suelto"}, {"role": "user", "content": "uno"}, {"role": "user", "content": "dos"},
         {"role": "assistant", "content": "tres"}, {"role": "user", "content": "   "}],
        system_prompt="Eres un agente.",
    )
    assert out == 'Thought: x\nAction: finish("ok")'
    req = srv.requests[0]
    assert req["path"] == "/v1/messages"
    assert req["headers"]["x-api-key"] == "sk-ant-test" and req["headers"]["anthropic-version"] == "2023-06-01"
    body = req["body"]
    assert body["model"] == "claude-sonnet-5-5" and body["system"] == "Eres un agente." and body["max_tokens"] >= 4096
    assert "temperature" not in body                                           # se deja el valor por defecto del modelo
    roles = [m["role"] for m in body["messages"]]
    assert roles[0] == "user" and all(a != b for a, b in zip(roles, roles[1:]))  # empieza por user y alterna
    assert body["messages"][0]["content"] == "Comienza."                         # no puede empezar por assistant
    assert "uno\n\ndos" in [m["content"] for m in body["messages"]]            # los user consecutivos se unen


def test_overloaded_and_rate_limited_responses_are_retried(fake):
    srv = fake([(529, {"error": {"type": "overloaded_error"}}, {}), (429, {"error": {}}, {"retry-after": "0"}), ok("por fin")])
    llm = AnthropicLLM(api_key="k", base_url=srv.url, retry_delay=0.0)
    assert llm.generate([{"role": "user", "content": "hola"}]) == "por fin"
    assert len(srv.requests) == 3


def test_errors_are_explained_and_retries_are_bounded(fake):
    srv = fake([(401, {"error": {"message": "invalid x-api-key"}}, {})])
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicLLM(api_key="mala", base_url=srv.url).generate([{"role": "user", "content": "x"}])

    srv2 = fake([(429, {"error": {}}, {"retry-after": "0"})] * 5)
    with pytest.raises(RuntimeError, match="429"):
        AnthropicLLM(api_key="k", base_url=srv2.url, retry_delay=0.0, max_retries=2).generate([{"role": "user", "content": "x"}])
    assert len(srv2.requests) == 3                                               # 1 intento + 2 reintentos


def test_factory_requires_a_key_and_never_picks_anthropic_automatically(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("praxeon.agent_llm.is_ollama_online", lambda *a, **k: False)

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        create_agent_llm(provider="anthropic")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    llm = create_agent_llm(provider="anthropic", model="claude-opus-5-5")
    assert isinstance(llm, AnthropicLLM) and llm.provider_name == "anthropic" and llm.model_name == "claude-opus-5-5"
    assert create_agent_llm(provider="anthropic").model_name == "claude-sonnet-5-5"   # modelo por defecto
    assert llm._max_tokens >= 8192                                                      # puede escribir archivos largos

    # con solo esa clave, «auto» NO la elige (evita gasto de pago sin avisar)
    assert isinstance(create_agent_llm(provider="auto"), SimulatedAgentLLM)


def test_custom_endpoint_does_not_inherit_the_environment_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secreta")
    with pytest.raises(ValueError, match="explícitamente"):
        create_agent_llm(provider="anthropic", base_url="https://proxy.ejemplo.com")
    llm = create_agent_llm(provider="anthropic", base_url="https://proxy.ejemplo.com", api_key="clave-propia")
    assert llm._api_key == "clave-propia" and llm.endpoint == "https://proxy.ejemplo.com/v1/messages"


def test_the_provider_is_listed_for_the_ui(tmp_path):
    from praxeon.server.dependencies import RuntimeApplicationService

    service = RuntimeApplicationService(db_dir=str(tmp_path / "db"))
    ids = [p["provider_id"] for p in service.get_available_providers()["llm_providers"]]
    assert "anthropic" in ids
