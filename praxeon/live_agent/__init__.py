"""Paquete live_agent de PRAXEON.

Módulo de razonamiento y ejecución en vivo para agentes autónomos bajo supervisión
cognitiva de PRAXEON y TypeSafe AI.
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
from praxeon.live_agent.runtime_client import LiveAgentRuntimeClient
from praxeon.live_agent.trajectory_controller import TrajectoryController

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
