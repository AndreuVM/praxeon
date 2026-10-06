"""Pruebas unitarias para la arquitectura modular de praxeon.live_agent."""

import pytest
from praxeon import live_agent
from praxeon.live_agent import (
    LiveAgentRuntimeClient,
    TrajectoryController,
    build_optimized_prompt,
    compact_conversation_history,
    decode_process_bytes,
    parse_llm_steps,
    prune_observation_output,
    run_live_agent,
    run_live_gemini_agent,
)
from praxeon.interceptor.proxy_middleware import JEVProxyMiddleware


def test_package_exports():
    """Verifica que el paquete praxeon.live_agent re-exporte todos los símbolos requeridos."""
    assert callable(decode_process_bytes)
    assert callable(prune_observation_output)
    assert callable(compact_conversation_history)
    assert callable(build_optimized_prompt)
    assert callable(parse_llm_steps)
    assert callable(run_live_agent)
    assert callable(run_live_gemini_agent)
    assert issubclass(TrajectoryController, object)
    assert issubclass(LiveAgentRuntimeClient, object)


def test_decode_process_bytes():
    assert decode_process_bytes(b"hello world") == "hello world"
    assert decode_process_bytes(b"") == ""
    # Latin-1 fallback test
    assert "\xe1" in decode_process_bytes(b"\xe1")


def test_prune_observation_output():
    short_text = "Salida corta"
    assert prune_observation_output(short_text, max_chars=100) == short_text

    long_text = "A" * 3000
    pruned = prune_observation_output(long_text, max_chars=500)
    assert len(pruned) < 3000
    assert "caracteres intermedios omitidos" in pruned


def test_trajectory_controller_circuit_breaker():
    tc = TrajectoryController(max_steps=5)
    assert tc.can_continue() is True

    # 1. Primer bloqueo
    is_breaker, directive, probe = tc.register_block_interception("Error 1")
    assert is_breaker is False
    assert tc.consecutive_blocks == 1

    # 2. Segundo bloqueo
    is_breaker, directive, probe = tc.register_block_interception("Error 2")
    assert is_breaker is False
    assert tc.consecutive_blocks == 2
    assert "ORIENTACIÓN" in directive or "ALERTA" in directive

    # 3. Tercer bloqueo activa circuit breaker
    is_breaker, directive, probe = tc.register_block_interception("Error 3")
    assert is_breaker is True
    assert tc.consecutive_blocks == 0
    assert probe is not None


def test_trajectory_controller_finish_validation():
    tc = TrajectoryController(max_steps=5)

    # Finalización evasiva rechazada
    valid, reason, obs = tc.validate_finish_action("Pendiente de leer archivos", "Analizar repo")
    assert valid is False
    assert reason == "evasive"
    assert "RECHAZADA" in obs

    # Eco de diagnóstico interno rechazado
    valid, reason, obs = tc.validate_finish_action("Acción bloqueada por el supervisor", "Analizar repo")
    assert valid is False
    assert reason == "meta_leakage"
    assert "RECHAZADA" in obs

    # Conclusión legítima aprobada
    valid, reason, obs = tc.validate_finish_action("El repositorio contiene 42 módulos y pasa todos los tests.", "Analizar repo")
    assert valid is True
    assert reason == ""
    assert tc.task_finished is True
    assert tc.final_summary == "El repositorio contiene 42 módulos y pasa todos los tests."


def test_runtime_client_initialization():
    client = LiveAgentRuntimeClient()
    middleware = client.ensure_task("Inspección de módulos")
    assert isinstance(middleware, JEVProxyMiddleware)
    assert client.middleware is middleware
