"""Paquete live_agent de PRAXEON.

Interfaz de terminal del agente en vivo (`praxeon live`): cada tarea es una misión del runtime con el bucle canónico
(``runtime_session.TerminalMission``).
"""

from __future__ import annotations

from praxeon.live_agent.llm_adapter import (
    build_optimized_prompt,
    compact_conversation_history,
    decode_process_bytes,
    parse_llm_steps,
    prune_observation_output,
)
from praxeon.live_agent.mission_runner import (
    console,
    main,
    run_live_agent,
    run_live_gemini_agent,
    run_live_session,
)

# Obsoletos (v2.0.0): se cargan solo si alguien los pide, para que importar el paquete no avise
_DEPRECATED = {
    "TrajectoryController": "praxeon.live_agent.trajectory_controller",
    "LiveAgentRuntimeClient": "praxeon.live_agent.runtime_client",
}


def __getattr__(name: str):
    if name in _DEPRECATED:
        import importlib

        return getattr(importlib.import_module(_DEPRECATED[name]), name)
    raise AttributeError(f"module 'praxeon.live_agent' has no attribute {name!r}")

__all__ = [
    "decode_process_bytes",
    "prune_observation_output",
    "compact_conversation_history",
    "build_optimized_prompt",
    "parse_llm_steps",
    "TrajectoryController",
    "LiveAgentRuntimeClient",
    "run_live_agent",
    "run_live_gemini_agent",
    "run_live_session",
    "main",
    "console",
]
