"""The MCP files Forge reads and writes must meet the suite's canonical schemas.

The AI Infrastructure Suite owns ``tool-approvals.v1``, ``mcp-servers.v1`` and
``tool-definition.v1``. This module checks, from this side:

* the copies packaged in ``forge/tools/mcp/`` have not drifted from the suite's;
* every approvals file and definition snapshot Forge writes validates against
  the suite's schema, and approvals also parse with the suite's own reader
  (which is stricter: canonical origins, uniqueness);
* every origin Forge records for a server is one the suite accepts;
* Forge's reader and the suite's schema refuse the same invalid server files.

Skips without the suite installed, unless ``AGENTFORGE_REQUIRE_SUITE_CONTRACT=1``
(set by the suite-contract job).
"""

from __future__ import annotations

import importlib
import json
import os
import tomllib
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from forge.tools.mcp.approvals import ToolApprovals, new_approval
from forge.tools.mcp.config import McpServerConfig, McpServersFile
from forge.tools.mcp.definitions import DefinitionStore
from forge.tools.mcp.hashing import definition_document, definition_sha256

REQUIRE_ENV = "AGENTFORGE_REQUIRE_SUITE_CONTRACT"
ROOT = Path(__file__).resolve().parents[2]
PACKAGED = ROOT / "forge" / "tools" / "mcp"
EXAMPLES = ROOT / "docs" / "examples" / "mcp"
NOW = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
SCHEMAS = ("tool-approvals.schema.json", "mcp-servers.schema.json", "tool-definition.schema.json")


def _import_or_skip(module: str) -> Any:
    try:
        return importlib.import_module(module)
    except ImportError:
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail(f"{module} is not installed and {REQUIRE_ENV}=1.")
        pytest.skip(f"{module} is not installed.", allow_module_level=True)


tool_inventory = _import_or_skip("ai_infrastructure_suite.tool_inventory")
jsonschema = _import_or_skip("jsonschema")


def _suite_schema(name: str) -> dict[str, Any]:
    try:
        raw = files("ai_infrastructure_suite").joinpath(f"data/{name}").read_text("utf-8")
    except FileNotFoundError:
        pytest.fail(f"The installed suite does not ship {name}.")
    schema: dict[str, Any] = json.loads(raw)
    return schema


def _without_identity(schema: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in schema.items() if key not in {"$id", "description"}}


def test_the_installed_suite_is_reported() -> None:
    import ai_infrastructure_suite

    print(f"validated against ai-infrastructure-suite {ai_infrastructure_suite.__version__}")


@pytest.mark.parametrize("name", SCHEMAS)
def test_packaged_schemas_have_not_drifted_from_the_suite(name: str) -> None:
    packaged = json.loads((PACKAGED / name).read_text("utf-8"))
    assert _without_identity(packaged) == _without_identity(_suite_schema(name))


def _approvals() -> ToolApprovals:
    digest = definition_sha256(
        "files", "search", description="Search.", input_schema={"type": "object"}
    )
    return ToolApprovals(
        [
            new_approval(
                server_id="files",
                server_origin="http://127.0.0.1:8765",
                tool_name="search",
                definition_sha256=digest,
                approved_by="alice",
                now=NOW,
            ),
            new_approval(
                server_id="local-tools",
                server_origin=None,
                tool_name="fs/read.file",
                definition_sha256="b" * 64,
                approved_by="Security review (SEC-142)",
                accepted_flags=["override_instructions", "role_override"],
                now=NOW,
            ),
            new_approval(
                server_id="crm",
                server_origin="https://mcp.crm.example.com",
                tool_name="lookup",
                definition_sha256="c" * 64,
                approved_by="bob",
                now=NOW,
            ),
        ]
    )


def test_written_approvals_meet_the_suite_schema_and_reader(tmp_path: Path) -> None:
    path = tmp_path / "tool-approvals.json"
    _approvals().save(path)
    jsonschema.validate(json.loads(path.read_bytes()), _suite_schema("tool-approvals.schema.json"))
    parsed = tool_inventory.parse_tool_approvals(path.read_bytes())
    assert len(parsed.approvals) == 3


@pytest.mark.parametrize(
    "path", sorted(EXAMPLES.glob("tool-approvals*.json")), ids=lambda p: p.name
)
def test_shipped_approvals_meet_the_suite_schema_and_reader(path: Path) -> None:
    jsonschema.validate(json.loads(path.read_bytes()), _suite_schema("tool-approvals.schema.json"))
    tool_inventory.parse_tool_approvals(path.read_bytes())


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/mcp",
        "http://localhost:8765/",
        "https://mcp.example.com/v1/mcp",
        "https://mcp.example.com:8443/mcp",
        "http://[::1]:8765/mcp",
        "HTTP://LocalHost:8765/MCP",
        "https://mcp.example.com:443/mcp",
    ],
)
def test_every_origin_forge_records_is_one_the_suite_accepts(url: str, tmp_path: Path) -> None:
    origin = McpServerConfig(server_id="srv", transport="streamable_http", url=url).origin
    approval = new_approval(
        server_id="srv",
        server_origin=origin,
        tool_name="t",
        definition_sha256="a" * 64,
        approved_by="alice",
        now=NOW,
    )
    path = tmp_path / "tool-approvals.json"
    ToolApprovals([approval]).save(path)
    tool_inventory.parse_tool_approvals(path.read_bytes())


def test_written_definition_snapshots_meet_the_suite_schema(tmp_path: Path) -> None:
    store = DefinitionStore(tmp_path / "tool-definitions")
    schema = _suite_schema("tool-definition.schema.json")
    documents = [
        definition_document(
            "files", "search", description="Search.", input_schema={"type": "object"}
        ),
        definition_document(
            "files",
            "count",
            description=None,
            input_schema={"type": "object", "properties": {"table": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"rows": {"type": "integer"}}},
            annotations={"readOnlyHint": True},
        ),
    ]
    for document in documents:
        digest = store.save(document)
        jsonschema.validate(json.loads(store.path(digest).read_bytes()), schema)
    for path in sorted((EXAMPLES / "tool-definitions").glob("*.json")):
        jsonschema.validate(json.loads(path.read_bytes()), schema)


def test_the_shipped_server_file_meets_the_suite_schema() -> None:
    path = EXAMPLES / "mcp-servers.toml"
    jsonschema.validate(
        tomllib.loads(path.read_text("utf-8")), _suite_schema("mcp-servers.schema.json")
    )
    McpServersFile.load(path)


@pytest.mark.parametrize(
    "server",
    [
        {"server_id": "x", "transport": "in_process"},
        {"server_id": "x", "transport": "stdio"},
        {"server_id": "X", "transport": "stdio", "command": "c"},
        {"server_id": "x", "transport": "streamable_http", "url": "https://u:p@mcp.example.com/"},
        {"server_id": "x", "transport": "streamable_http", "url": "https://mcp.example.com/?k=1"},
        {"server_id": "x", "transport": "stdio", "command": "c", "url": "http://127.0.0.1/"},
        {"server_id": "x", "transport": "stdio", "command": "c", "surprise": 1},
    ],
)
def test_forge_and_the_suite_refuse_the_same_server_entries(server: dict[str, Any]) -> None:
    document = {"schema_version": "mcp-servers.v1", "servers": [server]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(document, _suite_schema("mcp-servers.schema.json"))
    with pytest.raises(Exception):  # noqa: B017 - pydantic, ConfigurationError or ValueError
        McpServersFile.model_validate(document)


async def test_approvals_from_a_real_connector_meet_the_suite_reader(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from tests import mcp_fakes

    from forge.tools.mcp.connector import McpConnector

    config = McpServerConfig(server_id="files", transport="in_process")
    async with McpConnector(
        config, ToolApprovals(), server=mcp_fakes.honest().server()
    ) as connector:
        approvals = ToolApprovals()
        store = DefinitionStore(tmp_path / "tool-definitions")
        for found in connector.discovered():
            approvals = approvals.with_approval(connector.approve(found.name, approved_by="alice"))
            store.save(found.definition())
    path = tmp_path / "tool-approvals.json"
    approvals.save(path)
    parsed = tool_inventory.parse_tool_approvals(path.read_bytes())
    assert {a.tool_name for a in parsed.approvals} == {"search", "count"}
    schema = _suite_schema("tool-definition.schema.json")
    for snapshot in (tmp_path / "tool-definitions").iterdir():
        jsonschema.validate(json.loads(snapshot.read_bytes()), schema)
