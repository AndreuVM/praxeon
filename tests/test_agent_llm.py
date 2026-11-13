"""Pruebas unitarias para el cliente LLM desacoplado del Agente (Fase Multi-Provider)."""

import json
import os
import urllib.error
from unittest.mock import MagicMock, patch
import pytest

from praxeon.agent_llm import (
    BaseAgentLLM,
    GeminiLLM,
    OpenAICompatibleLLM,
    SimulatedAgentLLM,
    create_agent_llm,
    is_ollama_online,
)


def test_simulated_agent_llm():
    """Verifica que el agente simulador genere pasos coherentes secuencialmente."""
    sim = SimulatedAgentLLM(model_name="test-sim")
    assert sim.provider_name == "simulator"
    assert sim.model_name == "test-sim"

    step1 = sim.generate([{"role": "user", "content": "Hola"}])
    assert "Thought:" in step1
    assert "run_command" in step1

    step2 = sim.generate([{"role": "user", "content": "Continuar"}])
    assert "read_file" in step2

    step3 = sim.generate([{"role": "user", "content": "Continuar"}])
    assert "finish" in step3


def test_openai_compatible_endpoint_formatting():
    """Verifica que el endpoint de OpenAI-compatible se normalice a /v1/chat/completions."""
    client1 = OpenAICompatibleLLM(base_url="http://localhost:11434", model="qwen2.5-coder:7b")
    assert client1.endpoint == "http://localhost:11434/v1/chat/completions"

    client2 = OpenAICompatibleLLM(base_url="https://api.groq.com/openai/v1/", model="llama-3.3-70b-versatile")
    assert client2.endpoint == "https://api.groq.com/openai/v1/chat/completions"


@patch("praxeon.http_client.post_json")
def test_openai_compatible_generate_success(mock_post):
    """Verifica una llamada exitosa de generación compatible con OpenAI."""
    mock_post.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Thought: Voy a listar los archivos.\nAction: run_command(\"ls\")",
                }
            }
        ]
    }

    client = OpenAICompatibleLLM(
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        api_key="gsk-fake-key",
        provider_name="groq",
    )
    res = client.generate([{"role": "user", "content": "Comienza"}], system_prompt="Eres un agente.")
    assert "Thought: Voy a listar los archivos." in res
    assert 'run_command("ls")' in res


@patch("praxeon.http_client.post_json")
def test_openai_compatible_rate_limit_429(mock_post):
    """Verifica que el error 429 de cuota sea interceptado con mensaje descriptivo."""
    mock_post.side_effect = urllib.error.HTTPError(
        url="https://api.groq.com/openai/v1/chat/completions",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=MagicMock(read=lambda: b'{"error": {"message": "Rate limit reached"}}'),
    )

    client = OpenAICompatibleLLM(
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        api_key="gsk-fake-key",
        provider_name="groq",
    )
    with pytest.raises(RuntimeError) as exc_info:
        client.generate([{"role": "user", "content": "test"}])
    assert "Límite de cuota excedido (HTTP 429)" in str(exc_info.value)
    assert "Rate limit reached" in str(exc_info.value)


@patch("praxeon.http_client.post_json")
def test_ollama_connection_error(mock_post):
    """Verifica que si Ollama no está escuchando en local, dé un error claro con instrucciones."""
    mock_post.side_effect = urllib.error.URLError(reason="Connection refused")

    client = OpenAICompatibleLLM(
        base_url="http://localhost:11434/v1",
        model="qwen2.5-coder:7b",
        provider_name="ollama",
    )
    with pytest.raises(ConnectionError) as exc_info:
        client.generate([{"role": "user", "content": "test"}])
    assert "No se pudo conectar al servidor local" in str(exc_info.value)
    assert "ollama serve" in str(exc_info.value)


def test_create_agent_llm_factory():
    """Verifica la fábrica create_agent_llm con diversos proveedores."""
    # 1. Ollama
    ollama_llm = create_agent_llm(provider="ollama", model="qwen2.5-coder:14b")
    assert isinstance(ollama_llm, OpenAICompatibleLLM)
    assert ollama_llm.provider_name == "ollama"
    assert ollama_llm.model_name == "qwen2.5-coder:14b"

    # 2. Groq con clave explícita
    groq_llm = create_agent_llm(provider="groq", api_key="gsk-test")
    assert groq_llm.provider_name == "groq"
    assert groq_llm.model_name == "llama-3.3-70b-versatile"

    # 3. Groq sin clave debe fallar con mensaje claro
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError) as exc:
            create_agent_llm(provider="groq")
        assert "GROQ_API_KEY" in str(exc.value)

    # 4. OpenRouter con clave
    openrouter_llm = create_agent_llm(provider="openrouter", api_key="sk-or-test")
    assert openrouter_llm.provider_name == "openrouter"
    assert openrouter_llm.model_name == "qwen/qwen-2.5-coder-32b-instruct:free"

    # 5. Simulador
    sim_llm = create_agent_llm(provider="simulator")
    assert isinstance(sim_llm, SimulatedAgentLLM)


def test_create_agent_llm_auto_fallback():
    """Verifica que auto fallback seleccione el simulador si no hay ninguna clave ni servicio."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("praxeon.agent_llm.is_ollama_online", return_value=False):
            llm = create_agent_llm(provider="auto")
            assert isinstance(llm, SimulatedAgentLLM)


def test_create_agent_llm_auto_prefers_groq():
    """Verifica que auto priorice Groq si GROQ_API_KEY está presente."""
    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk-sample-key"}):
        llm = create_agent_llm(provider="auto")
        assert isinstance(llm, OpenAICompatibleLLM)
        assert llm.provider_name == "groq"
