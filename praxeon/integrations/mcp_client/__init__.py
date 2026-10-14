"""PRAXEON como CLIENTE MCP: consume herramientas de servidores MCP bajo la supervisión del runtime.

(El servidor MCP de PRAXEON, que expone su supervisión a agentes externos, está en `praxeon.integrations.mcp`.)
"""

from praxeon.integrations.mcp_client.client import McpError, StdioMcpClient
from praxeon.integrations.mcp_client.config import McpConfigStore, McpServerConfig, ToolOverride
from praxeon.integrations.mcp_client.manager import McpManager, namespaced

__all__ = ["McpConfigStore", "McpError", "McpManager", "McpServerConfig", "StdioMcpClient", "ToolOverride", "namespaced"]
