"""A tiny MCP server run over stdio by the transport tests. Not collected by pytest."""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("stdio-fake")


@server.tool()
def search(query: str) -> str:
    """Search the knowledge base."""
    return f"found: {query}"


@server.tool()
def visible(name: str) -> str:
    """Whether an environment variable reached this server."""
    return "yes" if name in os.environ else "no"


if __name__ == "__main__":
    server.run("stdio")
