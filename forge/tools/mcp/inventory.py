"""Write ``tool-inventory.v1`` for the ``tool_connectors`` vector.

The inventory lists the MCP tools agents can actually call: every tool a
connector offers, which means every **approved** tool. Pending, changed,
returned and invalid tools are not callable, so they are left out and reported
as skipped. Python tools defined in your own repository are not tool
connectors and are not listed.

Every row carries a ``backend``:

* from ``mcp-servers.toml`` (``backend``, or ``tool_backends`` per tool) for a
  configured server. A server that declares nothing is refused, never guessed;
* from the connector itself for a ``ToolConnector`` adapter. **An adapter tool
  is never written with ``backend: null``**: an adapter wraps something.

The document is checked here against the same rules the suite's reader
enforces (uniqueness of ``tool_id``, of server and name, and of definition
hashes) before it is written. AgentForge's ``suite-contract`` job feeds it to
the suite's own reader.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

from forge._version import __version__
from forge.exceptions import McpError
from forge.tools.mcp.backend import SELF_CONTAINED, inventory_backend
from forge.tools.mcp.connector import McpConnector, ToolState

SCHEMA_VERSION: Final = "tool-inventory.v1"
TRANSPORTS: Final = {
    "streamable_http": "mcp_streamable_http",
    "stdio": "mcp_stdio",
    "in_process": "in_process",
}


class InventoryError(McpError):
    """An inventory cannot be written truthfully."""


def inventory_rows(connector: McpConnector) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows for one connected server, and the tools skipped (with why)."""
    config = connector.config
    discovered = connector.discovered()
    offered = {found.name for found in discovered}
    unknown = sorted(set(config.tool_backends) - offered)
    if unknown:
        raise InventoryError(
            f"tool_backends for {config.server_id!r} names tools the server does not offer: "
            f"{', '.join(unknown)}."
        )
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for found in discovered:
        label = f"{config.server_id}/{found.name}"
        if found.state is not ToolState.APPROVED:
            skipped.append(f"{label} ({found.state.value})")
            continue
        declaration = config.backend_for(found.name)
        if declaration is None:
            raise InventoryError(
                f"{label} has no declared backend. Declare backend for server "
                f'{config.server_id!r} ("{SELF_CONTAINED}" or the service it depends on).'
            )
        if connector.adapter and declaration == SELF_CONTAINED:
            raise InventoryError(
                f"{label} is an adapter tool; an adapter tool always names its backend."
            )
        rows.append(
            {
                "tool_id": found.exposed_name,
                "name": found.name,
                "server_id": config.server_id,
                "transport": TRANSPORTS[config.transport],
                "server_origin": config.origin,
                "definition_sha256": found.definition_sha256,
                "portability": "mcp",
                "reconstruction": None,
                "backend": inventory_backend(declaration),
            }
        )
    return rows, skipped


def build_inventory(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """The ``tool-inventory.v1`` document, checked against the reader's cross-field rules."""
    tools = list(rows)
    if not tools:
        raise InventoryError(
            "No approved tools to inventory. Approve tools first, or declare the vector inapplicable."
        )
    for what, keys in (
        ("tool_id", [t["tool_id"] for t in tools]),
        ("server and name", [(t["server_id"], t["name"]) for t in tools]),
        ("definition hash", [t["definition_sha256"] for t in tools if t["definition_sha256"]]),
    ):
        if len(set(keys)) != len(keys):
            raise InventoryError(f"Two tools share a {what}.")
    for tool in tools:
        if "backend" not in tool:
            raise InventoryError(f"{tool['tool_id']} has no backend.")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": {"tool": "agentforge-oss", "version": __version__},
        "tools": tools,
    }


def render_inventory(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def write_inventory(document: dict[str, Any], path: Path) -> None:
    if path.is_symlink():
        raise InventoryError("The inventory path must not be a symbolic link.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_inventory(document), encoding="utf-8", newline="\n")
