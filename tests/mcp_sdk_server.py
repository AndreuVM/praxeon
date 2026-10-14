"""Servidor MCP escrito con el SDK oficial (`mcp`): comprueba que el cliente de PRAXEON habla con otra implementación."""

try:
    from mcp.server.mcpserver import MCPServer as _Server   # SDK 2.x
except ImportError:
    from mcp.server.fastmcp import FastMCP as _Server           # SDK 1.x

app = _Server("sdk-demo")


@app.tool()
def add(a: int, b: int) -> int:
    """Suma dos enteros."""
    return a + b


@app.tool()
def shout(text: str, times: int = 1) -> str:
    """Repite el texto en mayúsculas."""
    return (text.upper() + " ") * times


@app.tool()
def fail() -> str:
    """Siempre falla."""
    raise ValueError("fallo del SDK")


if __name__ == "__main__":
    app.run(transport="stdio")
