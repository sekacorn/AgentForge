"""The ``ToolConnector`` adapter: proprietary connectors behind an in-process MCP server.

A connector to a proprietary system implements three things::

    class CrmConnector:
        backend = Backend(system="crm-vendor-api", portability="vendor_only",
                          reconstruction="documented")

        def describe(self) -> list[ToolDescription]: ...
        async def call(self, name: str, arguments: dict) -> str | dict | list: ...

:func:`mount` puts it behind an in-process MCP server and returns an ordinary
:class:`~forge.tools.mcp.connector.McpConnector`. Every tool it wraps then
passes the same definition hashing, approval, bounds, argument validation and
result handling as any other MCP tool.

**The backend is required, and never "self-contained".** An adapter exists to
wrap something outside Forge, so it must say what: ``mount`` refuses a
connector without a :class:`~forge.tools.mcp.backend.Backend`, and
``forge tools inventory`` never writes an adapter tool with ``backend: null``.
A vendor-only backend scores 75 or 100 in the suite, whatever the MCP wrapper.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import mcp_types as types
from mcp.server.lowlevel import Server

from forge.exceptions import McpError
from forge.tools.mcp.approvals import ToolApprovals
from forge.tools.mcp.backend import Backend
from forge.tools.mcp.config import McpServerConfig
from forge.tools.mcp.connector import McpConnector


@dataclass(frozen=True)
class ToolDescription:
    """One tool a connector offers, as its MCP definition."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None = None
    annotations: Mapping[str, Any] | None = None


@runtime_checkable
class ToolConnector(Protocol):
    """A connector to a system outside Forge. ``backend`` has no default."""

    @property
    def backend(self) -> Backend: ...

    def describe(self) -> Sequence[ToolDescription]: ...

    async def call(self, name: str, arguments: dict[str, Any]) -> Any: ...


def _server(connector: ToolConnector, name: str) -> Server[Any]:
    async def on_list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=dict(tool.input_schema),
                    output_schema=None if tool.output_schema is None else dict(tool.output_schema),
                    annotations=None
                    if tool.annotations is None
                    else types.ToolAnnotations(**dict(tool.annotations)),
                )
                for tool in connector.describe()
            ]
        )

    async def on_call_tool(_ctx: Any, params: Any) -> types.CallToolResult:
        result = await connector.call(str(params.name), dict(params.arguments or {}))
        text = (
            result
            if isinstance(result, str)
            else json.dumps(result, ensure_ascii=False, default=str)
        )
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    return Server(name, on_list_tools=on_list_tools, on_call_tool=on_call_tool)


def mount(
    connector: ToolConnector,
    server_id: str,
    approvals: ToolApprovals,
    **connector_options: Any,
) -> McpConnector:
    """An :class:`McpConnector` for ``connector``, mounted in-process. Use it as a context manager."""
    backend = getattr(connector, "backend", None)
    if not isinstance(backend, Backend):
        raise McpError(
            f"The connector for {server_id!r} must declare its backend as a Backend "
            "(the system it wraps); an adapter is never self-contained."
        )
    config = McpServerConfig(server_id=server_id, transport="in_process", backend=backend)
    mounted = McpConnector(
        config,
        approvals,
        server=_server(connector, f"forge-adapter-{server_id}"),
        **connector_options,
    )
    mounted.adapter = True
    return mounted
