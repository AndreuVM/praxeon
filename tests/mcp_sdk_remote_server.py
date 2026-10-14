"""Servidor MCP remoto con el SDK oficial (`mcp`): SSE clásico o HTTP streamable. Uso: <sse|streamable> <puerto>."""

import sys

import uvicorn

try:
    from mcp.server.mcpserver import MCPServer as _Server   # SDK 2.x
except ImportError:
    from mcp.server.fastmcp import FastMCP as _Server       # SDK 1.x

app = _Server("sdk-remote")


@app.tool()
def add(a: int, b: int) -> int:
    """Suma dos enteros."""
    return a + b


@app.tool()
def shout(text: str) -> str:
    """Pone el texto en mayúsculas."""
    return text.upper()


if __name__ == "__main__":
    kind, port = sys.argv[1], int(sys.argv[2])
    asgi = app.sse_app() if kind == "sse" else app.streamable_http_app()
    uvicorn.run(asgi, host="127.0.0.1", port=port, log_level="error")
