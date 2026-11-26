"""What Forge knows about its MCP tools must fit the suite's ``tool-inventory.v1``.

``forge tools inventory`` (the writer) comes later. This module pins the parts
of the contract that exist already, so the writer cannot be built on a
mismatch: the names a connector exposes, the origins and definition hashes it
records, and the required ``backend`` on every tool (``null`` when
self-contained). It also pins the owner rule Forge's adapter will rely on:
the suite scores any tool with a vendor-only backend at 75 or 100, whatever its
MCP interface, approval or hosting.

Skips without the suite installed, unless ``AGENTFORGE_REQUIRE_SUITE_CONTRACT=1``.
"""

from __future__ import annotations

import importlib
import json
import os
from importlib.resources import files
from typing import Any

import pytest

from forge.tools.mcp.approvals import ToolApprovals
from forge.tools.mcp.config import McpServerConfig

REQUIRE_ENV = "AGENTFORGE_REQUIRE_SUITE_CONTRACT"


def _import_or_skip(module: str) -> Any:
    try:
        return importlib.import_module(module)
    except ImportError:
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail(f"{module} is not installed and {REQUIRE_ENV}=1.")
        pytest.skip(f"{module} is not installed.", allow_module_level=True)


tool_inventory = _import_or_skip("ai_infrastructure_suite.tool_inventory")
tool_vector = _import_or_skip("ai_infrastructure_suite.tool_vector")
jsonschema = _import_or_skip("jsonschema")

VENDOR_BACKEND = {
    "system": "crm-vendor-api",
    "portability": "vendor_only",
    "reconstruction": "none",
}


def _schema() -> dict[str, Any]:
    raw = (
        files("ai_infrastructure_suite")
        .joinpath("data/tool-inventory.schema.json")
        .read_text("utf-8")
    )
    schema: dict[str, Any] = json.loads(raw)
    return schema


def test_the_suite_requires_a_backend_on_every_tool() -> None:
    assert "backend" in _schema()["$defs"]["tool"]["required"]


async def _discovered(config: McpServerConfig) -> tuple[list[Any], ToolApprovals]:
    pytest.importorskip("mcp")
    from tests import mcp_fakes

    from forge.tools.mcp.connector import McpConnector

    in_process = McpServerConfig(server_id=config.server_id, transport="in_process")
    async with McpConnector(in_process, ToolApprovals(), server=mcp_fakes.honest().server()) as c:
        approvals = ToolApprovals()
        for found in c.discovered():
            approvals = approvals.with_approval(c.approve(found.name, approved_by="alice"))
        return c.discovered(), approvals


def _entry(found: Any, config: McpServerConfig, backend: dict[str, Any] | None) -> dict[str, Any]:
    """The inventory row a writer would produce from what the connector knows."""
    return {
        "tool_id": found.exposed_name,
        "name": found.name,
        "server_id": config.server_id,
        "transport": {"streamable_http": "mcp_streamable_http", "stdio": "mcp_stdio"}.get(
            config.transport, "in_process"
        ),
        "server_origin": config.origin,
        "definition_sha256": found.definition_sha256,
        "portability": "mcp",
        "reconstruction": None,
        "backend": backend,
    }


@pytest.mark.parametrize(
    "config",
    [
        McpServerConfig(server_id="files", transport="stdio", command="files-mcp"),
        McpServerConfig(
            server_id="files", transport="streamable_http", url="http://127.0.0.1:8765/mcp"
        ),
    ],
    ids=["stdio", "loopback-http"],
)
async def test_connector_tools_fit_the_suite_inventory_reader(config: McpServerConfig) -> None:
    discovered, _ = await _discovered(config)
    document = {
        "schema_version": "tool-inventory.v1",
        "generated_by": {"tool": "agentforge-oss", "version": "contract"},
        "tools": [_entry(found, config, None) for found in discovered],
    }
    jsonschema.validate(document, _schema())
    parsed = tool_inventory.parse_tool_inventory(json.dumps(document).encode())
    assert {t.tool_id for t in parsed.tools} == {"files__search", "files__count"}
    assert all(t.backend is None for t in parsed.tools)


async def test_a_vendor_backend_scores_vendor_only_even_when_approved_and_local() -> None:
    config = McpServerConfig(server_id="files", transport="stdio", command="files-mcp")
    discovered, approvals = await _discovered(config)
    rows = [_entry(found, config, VENDOR_BACKEND) for found in discovered]
    document = {
        "schema_version": "tool-inventory.v1",
        "generated_by": {"tool": "agentforge-oss", "version": "contract"},
        "tools": rows,
    }
    jsonschema.validate(document, _schema())
    parsed = tool_inventory.parse_tool_inventory(json.dumps(document).encode())
    suite_approvals = tool_inventory.ToolApprovals(
        document_sha256="0" * 64,
        approvals=tuple(
            tool_inventory.ToolApproval(
                server_id=a.server_id,
                server_origin=a.server_origin,
                tool_name=a.tool_name,
                definition_sha256=a.definition_sha256,
                approved_by=a.approved_by,
                approved_at=a.approved_at,
                accepted_flags=a.accepted_flags,
            )
            for a in approvals
        ),
    )
    for tool in parsed.tools:
        row = tool_vector.score_tool(tool, suite_approvals)
        assert row["approval"] == "approved" and row["hosting"] == "local"
        assert row["score"] == 100


def test_a_tool_without_a_backend_key_is_refused() -> None:
    row = {
        "tool_id": "files__search",
        "name": "search",
        "server_id": "files",
        "transport": "mcp_stdio",
        "server_origin": None,
        "definition_sha256": "a" * 64,
        "portability": "mcp",
        "reconstruction": None,
    }
    document = {
        "schema_version": "tool-inventory.v1",
        "generated_by": {"tool": "t", "version": "v"},
        "tools": [row],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(document, _schema())
    with pytest.raises(ValueError):
        tool_inventory.parse_tool_inventory(json.dumps(document).encode())
