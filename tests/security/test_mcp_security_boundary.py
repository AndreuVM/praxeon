"""Prueba end-to-end de frontera de seguridad MCP (tests/security/test_mcp_security_boundary.py).

Demuestra formalmente que una tools/call bloqueada o no autorizada a través de MCP
jamás alcanza el handler físico de ejecución ni genera mutaciones en el sistema.
"""

from unittest.mock import MagicMock
import pytest

from praxeon.domain.models import ActionCandidate, DecisionStatus, Goal, ToolCall
from praxeon.interceptor.mcp_bridge import MCPBridge
from praxeon.providers.replay import ReplayProvider
from praxeon.runtime.executor import SecureExecutor
from praxeon.runtime.navigator import Navigator


def test_mcp_blocked_tool_call_never_touches_physical_executor():
    """Verifica que una llamada a jev_v2_step_and_execute rechazada nunca invoque execute()."""
    # 1. Configurar un ejecutor con espía/mock en execute
    mock_executor = SecureExecutor(dry_run=True)
    mock_executor.execute = MagicMock(side_effect=mock_executor.execute)

    provider = ReplayProvider()
    navigator = Navigator(provider=provider, executor=mock_executor)
    bridge = MCPBridge(navigator=navigator)

    # Iniciar sesión
    bridge.v2_start_session(goal="Prueba de contención MCP", session_id="mcp_sec_test")

    # Acción peligrosa no autorizada (comando crítico destructivo)
    destructive_action = {
        "id": "mcp_act_evil",
        "description": "Destructive shell command",
        "tool_call": {
            "tool_name": "run_command",
            "arguments": {"command": "rm -rf /"},
        },
        "requires_evidence": [],
    }

    # Invocación a través del bridge
    res = bridge.v2_step_and_execute(action=destructive_action)

    # 2. Comprobaciones de seguridad
    assert res["decision"]["status"] == DecisionStatus.BLOCK.value
    assert res["observation"] is None  # Sin observación física
    assert "CRITICAL_OPERATIONAL_RISK" in res["decision"]["reason_codes"]

    # Invariante crítico: execute() NUNCA debió ser invocado
    mock_executor.execute.assert_not_called()


def test_mcp_unconfirmed_high_risk_action_never_executes():
    """Verifica que una acción que requiere confirmación humana sea detenida antes del handler."""
    mock_executor = SecureExecutor(dry_run=True)
    mock_executor.execute = MagicMock(side_effect=mock_executor.execute)

    provider = ReplayProvider()
    navigator = Navigator(provider=provider, executor=mock_executor)
    bridge = MCPBridge(navigator=navigator)

    bridge.v2_start_session(goal="Audit sensitive tools", session_id="mcp_unconfirmed")

    sensitive_action = {
        "id": "act_sensitive_delete",
        "description": "Delete production config",
        "tool_call": {
            "tool_name": "delete_file",
            "arguments": {"path": "production.yaml"},
        },
        "requires_evidence": [],
    }

    res = bridge.v2_step_and_execute(action=sensitive_action)
    assert res["decision"]["status"] == DecisionStatus.BLOCK.value
    assert res["observation"] is None
    mock_executor.execute.assert_not_called()
