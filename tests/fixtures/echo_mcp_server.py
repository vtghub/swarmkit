"""A tiny real MCP server (stdio) used as a test fixture for
mcp_server/client_tools.py — proves the generic-server consumption path
without depending on any specific external server being installed. The
real, hands-on integration proof against an actual third-party server
(vtghub/mcp-native-core) was done manually while building client_tools.py;
this fixture keeps the automated suite self-contained.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(name="echo-test-server")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the given text back verbatim."""
    return text


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


if __name__ == "__main__":
    mcp.run(transport="stdio")
