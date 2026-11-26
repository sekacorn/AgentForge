"""The ToolConnector adapter and ``forge tools inventory`` (last 4b checkpoint).

Every inventoried tool carries a backend, and an adapter tool can never be
written with ``backend: null``: ``mount`` refuses a connector without a real
backend, and the writer refuses again if that were ever bypassed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from tests import mcp_fakes  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from forge.cli.main import app as forge_app  # noqa: E402
from forge.exceptions import McpError  # noqa: E402
from forge.tools.mcp import cli as mcp_cli  # noqa: E402
from forge.tools.mcp.adapter import ToolDescription, mount  # noqa: E402
from forge.tools.mcp.approvals import ToolApprovals  # noqa: E402
from forge.tools.mcp.backend import SELF_CONTAINED, Backend  # noqa: E402
from forge.tools.mcp.config import McpServerConfig  # noqa: E402
from forge.tools.mcp.connector import McpConnector, ToolState  # noqa: E402
from forge.tools.mcp.inventory import (  # noqa: E402
    InventoryError,
    build_inventory,
    inventory_rows,
)

CRM = Backend(system="crm-vendor-api", portability="vendor_only", reconstruction="documented")
LOOKUP_SCHEMA = {
    "type": "object",
    "properties": {"customer": {"type": "string"}},
    "required": ["customer"],
    "additionalProperties": False,
}


class CrmConnector:
    """A proprietary connector, as an operator would write one."""

    backend = CRM

    def __init__(self) -> None:
        self.description = "Look up a customer record."
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def describe(self) -> list[ToolDescription]:
        return [
            ToolDescription(
                "lookup", self.description, LOOKUP_SCHEMA, annotations={"readOnlyHint": True}
            )
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        return {"customer": arguments["customer"], "tier": "gold"}


async def _approved(connector: McpConnector) -> ToolApprovals:
    approvals = ToolApprovals()
    for found in connector.discovered():
        approvals = approvals.with_approval(connector.approve(found.name, approved_by="alice"))
    return approvals


# --- the adapter --------------------------------------------------------------------------


async def test_an_adapter_tool_is_hashed_approved_and_called_like_any_mcp_tool() -> None:
    crm = CrmConnector()
    async with mount(crm, "crm", ToolApprovals()) as c:
        (found,) = c.discovered()
        assert found.state is ToolState.PENDING and c.tools() == []
        approvals = await _approved(c)
    async with mount(crm, "crm", approvals) as c:
        (lookup,) = c.tools()
        assert lookup.name == "crm__lookup" and lookup.dangerous
        text = await lookup.invoke({"customer": "c-1"})
    assert text.endswith('{"customer": "c-1", "tier": "gold"}')
    assert crm.calls == [("lookup", {"customer": "c-1"})]


async def test_an_adapter_definition_change_is_refused_until_reapproved() -> None:
    crm = CrmConnector()
    async with mount(crm, "crm", ToolApprovals()) as c:
        approvals = await _approved(c)
    crm.description = "Look up a customer record and export it."
    async with mount(crm, "crm", approvals) as c:
        assert c.discovered()[0].state is ToolState.CHANGED and c.tools() == []


@pytest.mark.parametrize(
    "backend", [None, SELF_CONTAINED, {"system": "x", "portability": "vendor_only"}]
)
def test_mount_refuses_a_connector_without_a_real_backend(backend: Any) -> None:
    class Unbacked(CrmConnector):
        pass

    Unbacked.backend = backend  # type: ignore[assignment]
    with pytest.raises(McpError, match="must declare its backend"):
        mount(Unbacked(), "crm", ToolApprovals())


def test_backend_declarations_are_validated() -> None:
    with pytest.raises(ValueError, match="identifier"):
        Backend(system="https://api.example.com", portability="vendor_only", reconstruction="none")
    with pytest.raises(ValueError, match="documented or none"):
        Backend(system="crm", portability="vendor_only")
    with pytest.raises(ValueError, match="no reconstruction"):
        Backend(system="pg", portability="mcp", reconstruction="none")


# --- inventory rows ----------------------------------------------------------------------


async def test_adapter_rows_carry_the_connectors_backend() -> None:
    crm = CrmConnector()
    async with mount(crm, "crm", ToolApprovals()) as c:
        approvals = await _approved(c)
    async with mount(crm, "crm", approvals) as c:
        rows, skipped = inventory_rows(c)
    (row,) = rows
    assert skipped == []
    assert row == {
        "tool_id": "crm__lookup",
        "name": "lookup",
        "server_id": "crm",
        "transport": "in_process",
        "server_origin": None,
        "definition_sha256": row["definition_sha256"],
        "portability": "mcp",
        "reconstruction": None,
        "backend": {
            "system": "crm-vendor-api",
            "portability": "vendor_only",
            "reconstruction": "documented",
        },
    }


async def test_an_adapter_tool_is_never_written_with_a_null_backend() -> None:
    """Even if mount's check were bypassed, the writer refuses."""
    fake = mcp_fakes.honest()
    config = McpServerConfig(server_id="files", transport="in_process", backend=SELF_CONTAINED)
    async with McpConnector(config, ToolApprovals(), server=fake.server()) as c:
        approvals = await _approved(c)
    async with McpConnector(config, approvals, server=fake.server()) as c:
        c.adapter = True
        with pytest.raises(InventoryError, match="adapter tool always names its backend"):
            inventory_rows(c)


async def test_only_approved_tools_are_listed_and_the_rest_reported() -> None:
    fake = mcp_fakes.honest()
    config = McpServerConfig(server_id="files", transport="in_process", backend=SELF_CONTAINED)
    async with McpConnector(config, ToolApprovals(), server=fake.server()) as c:
        approvals = ToolApprovals([c.approve("search", approved_by="alice")])
    async with McpConnector(config, approvals, server=fake.server()) as c:
        rows, skipped = inventory_rows(c)
    assert [r["tool_id"] for r in rows] == ["files__search"] and rows[0]["backend"] is None
    assert skipped == ["files/count (pending)"]


async def test_tool_backends_override_the_server_backend() -> None:
    fake = mcp_fakes.honest()
    geo = Backend(system="geocoder-saas", portability="vendor_only", reconstruction="none")
    config = McpServerConfig(
        server_id="files",
        transport="in_process",
        backend=SELF_CONTAINED,
        tool_backends={"count": geo},
    )
    async with McpConnector(config, ToolApprovals(), server=fake.server()) as c:
        approvals = await _approved(c)
    async with McpConnector(config, approvals, server=fake.server()) as c:
        rows, _ = inventory_rows(c)
    backends = {r["name"]: r["backend"] for r in rows}
    assert backends == {"search": None, "count": geo.to_inventory()}


async def test_an_undeclared_backend_is_refused_never_guessed() -> None:
    fake = mcp_fakes.honest()
    config = McpServerConfig(server_id="files", transport="in_process")
    async with McpConnector(config, ToolApprovals(), server=fake.server()) as c:
        approvals = await _approved(c)
    async with McpConnector(config, approvals, server=fake.server()) as c:
        with pytest.raises(InventoryError, match="no declared backend"):
            inventory_rows(c)


async def test_tool_backends_for_unknown_tools_are_refused() -> None:
    config = McpServerConfig(
        server_id="files",
        transport="in_process",
        backend=SELF_CONTAINED,
        tool_backends={"ghost": CRM},
    )
    async with McpConnector(config, ToolApprovals(), server=mcp_fakes.honest().server()) as c:
        with pytest.raises(InventoryError, match="ghost"):
            inventory_rows(c)


def test_build_inventory_refuses_empty_and_duplicate_documents() -> None:
    with pytest.raises(InventoryError, match="No approved tools"):
        build_inventory([])
    row = {
        "tool_id": "a__b",
        "name": "b",
        "server_id": "a",
        "transport": "mcp_stdio",
        "server_origin": None,
        "definition_sha256": "a" * 64,
        "portability": "mcp",
        "reconstruction": None,
        "backend": None,
    }
    with pytest.raises(InventoryError, match="share a tool_id"):
        build_inventory([row, dict(row)])
    document = build_inventory([row])
    assert document["schema_version"] == "tool-inventory.v1"
    assert document["generated_by"]["tool"] == "agentforge-oss"


# --- forge tools inventory ---------------------------------------------------------------

SERVERS = """
schema_version = "mcp-servers.v1"

[[servers]]
server_id = "files"
transport = "stdio"
command = "unused-in-tests"
backend = "self-contained"

[[servers]]
server_id = "wiki"
transport = "stdio"
command = "unused-in-tests"
backend = { system = "wiki-saas", portability = "vendor_only", reconstruction = "documented" }
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "mcp-servers.toml").write_text(SERVERS, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fakes = {"files": mcp_fakes.honest(), "wiki": mcp_fakes.honest()}

    def open_connector(server: McpServerConfig, approvals: ToolApprovals) -> McpConnector:
        config = server.model_copy(update={"transport": "in_process", "command": None})
        return McpConnector(config, approvals, server=fakes[server.server_id].server())

    monkeypatch.setattr(mcp_cli, "_open_connector", open_connector)
    return tmp_path


async def _approve_files(workspace: Path) -> None:
    config = McpServerConfig(server_id="files", transport="in_process", backend=SELF_CONTAINED)
    async with McpConnector(config, ToolApprovals(), server=mcp_fakes.honest().server()) as c:
        approvals = ToolApprovals([c.approve("search", approved_by="alice")])
    async with McpConnector(
        McpServerConfig(server_id="wiki", transport="in_process", backend=CRM),
        ToolApprovals(),
        server=mcp_fakes.honest().server(),
    ) as c:
        approvals = approvals.with_approval(c.approve("count", approved_by="alice"))
    approvals.save(workspace / "tool-approvals.json")


def test_forge_tools_inventory_writes_every_backend(workspace: Path) -> None:
    asyncio.run(_approve_files(workspace))
    result = CliRunner().invoke(
        forge_app, ["tools", "inventory", "--output", "tool-inventory.json"]
    )
    assert result.exit_code == 0, result.output
    document = json.loads((workspace / "tool-inventory.json").read_text(encoding="utf-8"))
    rows = {r["tool_id"]: r for r in document["tools"]}
    assert set(rows) == {"files__search", "wiki__count"}
    assert rows["files__search"]["backend"] is None
    assert rows["wiki__count"]["backend"] == {
        "system": "wiki-saas",
        "portability": "vendor_only",
        "reconstruction": "documented",
    }
    assert all("backend" in r for r in document["tools"])
    assert "skipped files/count (pending)" in result.output


def test_forge_tools_inventory_to_stdout(workspace: Path) -> None:
    asyncio.run(_approve_files(workspace))
    result = CliRunner().invoke(forge_app, ["tools", "inventory", "--server", "files"])
    assert result.exit_code == 0
    start = result.output.index("{")
    document = json.loads(result.output[start : result.output.rindex("}") + 1])
    assert [r["tool_id"] for r in document["tools"]] == ["files__search"]


def test_forge_tools_inventory_refuses_a_server_without_a_backend(workspace: Path) -> None:
    (workspace / "mcp-servers.toml").write_text(
        SERVERS.replace('backend = "self-contained"\n', ""), encoding="utf-8"
    )
    result = CliRunner().invoke(forge_app, ["tools", "inventory"])
    assert result.exit_code == 2 and "declares backend" in " ".join(result.output.split())


def test_forge_tools_inventory_with_nothing_approved(workspace: Path) -> None:
    result = CliRunner().invoke(forge_app, ["tools", "inventory"])
    assert result.exit_code == 2 and "No approved tools" in result.output
