"""Servidor Model Context Protocol (MCP) para integración formal de PRAXEON 1.0.

Expone las herramientas y diagnósticos de supervisión cognitiva de PRAXEON:
- praxeon_start_session (alias: jev_v2_start_session)
- praxeon_validate_action / praxeon_evaluate_action (alias: jev_v2_evaluate_action)
- praxeon_step_and_execute (alias: jev_v2_step_and_execute)
- praxeon_rollback (alias: jev_v2_rollback)
- praxeon_get_session_state (alias: jev_v2_get_session_state)
- praxeon_confirm_action (alias: jev_v2_confirm_action)
- praxeon_evaluate_next_step (alias: jev_evaluate_next_step)
- praxeon_evaluate_step_chunk (alias: jev_evaluate_step_chunk)
- praxeon_diagnose_trace (alias: jev_diagnose_trace)
"""


import sys
from typing import Optional

from praxeon.config import PraxeonConfig, default_config
from praxeon.interceptor.mcp_bridge import MCPBridge
from praxeon.runtime.navigator import Navigator


class MCPServer(MCPBridge):
    """Servidor MCP formal bajo la topología de integraciones v0.2."""

    def __init__(
        self,
        config: Optional[PraxeonConfig] = None,
        navigator: Optional[Navigator] = None,
    ):
        super().__init__(config=config, navigator=navigator)


def main() -> None:
    """Punto de entrada CLI para ejecutar el servidor MCP sobre stdio."""
    server = MCPServer()
    server.run_stdio_server()


if __name__ == "__main__":
    main()
