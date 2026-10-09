"""Tests unitarios para verificar la emisión de DeprecationWarning en entrypoints legacy jev-*."""

import pytest
from unittest.mock import patch

from praxeon.legacy_cli import (
    jev_nav_main,
    jev_live_main,
    jev_dash_main,
    jev_mcp_main,
)


@pytest.mark.unit
def test_jev_nav_emits_deprecation_warning():
    """Verifica que jev_nav_main emita DeprecationWarning y delegue a praxeon.cli.main."""
    with patch("praxeon.cli.main", return_value=0) as mock_main:
        with pytest.deprecated_call() as warning_info:
            res = jev_nav_main()
            assert res == 0
            mock_main.assert_called_once()
            assert any("jev-nav" in str(w.message) and "praxeon" in str(w.message) for w in warning_info)


@pytest.mark.unit
def test_jev_live_emits_deprecation_warning():
    """Verifica que jev_live_main emita DeprecationWarning y delegue a praxeon.live_agent.main."""
    with patch("praxeon.live_agent.main", return_value=0) as mock_main:
        with pytest.deprecated_call() as warning_info:
            res = jev_live_main()
            assert res == 0
            mock_main.assert_called_once()
            assert any("jev-live" in str(w.message) and "praxeon-live" in str(w.message) for w in warning_info)


@pytest.mark.unit
def test_jev_dash_emits_deprecation_warning():
    """Verifica que jev_dash_main emita DeprecationWarning y delegue a praxeon.dashboard.main."""
    with patch("praxeon.dashboard.main", return_value=0) as mock_main:
        with pytest.deprecated_call() as warning_info:
            res = jev_dash_main()
            assert res == 0
            mock_main.assert_called_once()
            assert any("jev-dash" in str(w.message) and "praxeon-dash" in str(w.message) for w in warning_info)


@pytest.mark.unit
def test_jev_mcp_emits_deprecation_warning():
    """Verifica que jev_mcp_main emita DeprecationWarning y delegue a praxeon.integrations.mcp.server.main."""
    with patch("praxeon.integrations.mcp.server.main", return_value=0) as mock_main:
        with pytest.deprecated_call() as warning_info:
            res = jev_mcp_main()
            assert res == 0
            mock_main.assert_called_once()
            assert any("jev-mcp" in str(w.message) and "praxeon-mcp" in str(w.message) for w in warning_info)
