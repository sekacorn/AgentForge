"""MCP tool connectors (optional; install ``agentforge-oss[mcp]``).

Core AgentForge never imports this package, and nothing outside it imports the
``mcp`` SDK. Hashing, approvals and server configuration are standard library
(plus pydantic) and import without the SDK; the connector and OAuth provider
need it and are loaded on first use, with a clear error when it is missing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from forge.tools.mcp.approvals import ApprovalsError, ToolApproval, ToolApprovals, new_approval
from forge.tools.mcp.config import McpServerConfig
from forge.tools.mcp.hashing import canonical_json, definition_sha256

if TYPE_CHECKING:
    from forge.tools.mcp.auth import ForgeOAuthProvider, require_iss
    from forge.tools.mcp.connector import DiscoveredTool, McpConnector, McpTool, ToolState

_LAZY = {
    "McpConnector": "forge.tools.mcp.connector",
    "McpTool": "forge.tools.mcp.connector",
    "DiscoveredTool": "forge.tools.mcp.connector",
    "ToolState": "forge.tools.mcp.connector",
    "ForgeOAuthProvider": "forge.tools.mcp.auth",
    "require_iss": "forge.tools.mcp.auth",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module 'forge.tools.mcp' has no attribute {name!r}")
    import importlib

    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name is not None and exc.name.split(".")[0] in {"mcp", "mcp_types", "httpx2"}:
            raise ImportError(
                "MCP connectors need the optional SDK: pip install 'agentforge-oss[mcp]'"
            ) from exc
        raise
    return getattr(module, name)


__all__ = [
    "ApprovalsError",
    "DiscoveredTool",
    "ForgeOAuthProvider",
    "McpConnector",
    "McpServerConfig",
    "McpTool",
    "ToolApproval",
    "ToolApprovals",
    "ToolState",
    "canonical_json",
    "definition_sha256",
    "new_approval",
    "require_iss",
]
