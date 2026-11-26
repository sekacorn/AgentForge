"""tool-approvals.v1, schema bounds and server configuration. No SDK needed."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from forge.exceptions import ConfigurationError
from forge.tools.mcp.approvals import (
    SCHEMA_PATH,
    ApprovalsError,
    ToolApprovals,
    new_approval,
)
from forge.tools.mcp.bounds import (
    MAX_DESCRIPTION_CHARS,
    SchemaBoundsError,
    check_input_schema,
    exposed_name,
    model_text,
)
from forge.tools.mcp.config import McpServerConfig

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "docs" / "examples" / "mcp"
DIGEST = "a" * 64


def _entry(**overrides: Any) -> dict[str, Any]:
    entry = {
        "server_id": "files",
        "server_origin": "http://127.0.0.1:8765",
        "tool_name": "search",
        "definition_sha256": DIGEST,
        "approved_by": "alice",
        "approved_at": "2026-11-26T09:00:00Z",
        "accepted_flags": [],
    }
    entry.update(overrides)
    return entry


def _doc(*entries: dict[str, Any]) -> bytes:
    return json.dumps({"schema_version": "tool-approvals.v1", "approvals": list(entries)}).encode()


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_fixtures_are_valid_under_the_schema_and_the_reader() -> None:
    schema = _schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    fixtures = sorted(FIXTURES.glob("tool-approvals*.json"))
    assert fixtures
    for path in fixtures:
        jsonschema.validate(json.loads(path.read_bytes()), schema)
        assert len(ToolApprovals.load(path)) >= 1


def test_round_trip_is_deterministic(tmp_path: Path) -> None:
    approvals = ToolApprovals.parse(_doc(_entry(tool_name="b"), _entry(tool_name="a")))
    path = tmp_path / "tool-approvals.json"
    approvals.save(path)
    first = path.read_bytes()
    ToolApprovals.load(path).save(path)
    assert path.read_bytes() == first
    assert [a.tool_name for a in ToolApprovals.load(path)] == ["a", "b"]
    jsonschema.validate(json.loads(first), _schema())


def test_matching_needs_server_origin_name_and_hash() -> None:
    approvals = ToolApprovals.parse(_doc(_entry()))
    assert approvals.is_approved("files", "http://127.0.0.1:8765", "search", DIGEST)
    assert not approvals.is_approved("files", "http://127.0.0.1:9999", "search", DIGEST)
    assert not approvals.is_approved("files", "http://127.0.0.1:8765", "search", "b" * 64)
    assert not approvals.is_approved("other", "http://127.0.0.1:8765", "search", DIGEST)
    assert not approvals.is_approved("files", "http://127.0.0.1:8765", "find", DIGEST)


def test_a_missing_file_approves_nothing(tmp_path: Path) -> None:
    assert len(ToolApprovals.load(tmp_path / "absent.json")) == 0


@pytest.mark.parametrize(
    ("document", "match"),
    [
        (b"{", "not valid"),
        (b'{"schema_version": "tool-approvals.v2", "approvals": []}', "not tool-approvals.v1"),
        (b'{"schema_version": "tool-approvals.v1", "approvals": [], "extra": 1}', "exactly"),
        (
            b'{"schema_version": "tool-approvals.v1", "schema_version": "tool-approvals.v1", "approvals": []}',
            "duplicate key",
        ),
        (_doc(_entry(definition_sha256="A" * 64)), "definition_sha256"),
        (_doc(_entry(server_origin="http://user:pw@host")), "server_origin"),
        (_doc(_entry(server_origin="http://host/path")), "origin"),
        (_doc(_entry(server_id="Files")), "server_id"),
        (_doc(_entry(approved_at="2026-11-26 09:00")), "approved_at"),
        (_doc(_entry(accepted_flags=["x", "x"])), "accepted_flags"),
        (_doc({**_entry(), "note": "x"}), "exactly the keys"),
        (_doc(_entry(), _entry()), "same server and tool"),
        (b'{"schema_version": "tool-approvals.v1", "approvals": [NaN]}', "NaN"),
    ],
)
def test_invalid_files_are_rejected_whole(document: bytes, match: str) -> None:
    with pytest.raises(ApprovalsError, match=match):
        ToolApprovals.parse(document)


def test_reader_and_schema_agree_on_rejections() -> None:
    schema = _schema()
    for bad in (
        _entry(definition_sha256="A" * 64),
        _entry(server_origin="http://host/path"),
        _entry(server_id="Files"),
        {**_entry(), "note": "x"},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"schema_version": "tool-approvals.v1", "approvals": [bad]}, schema)


def test_a_linked_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    target.write_bytes(_doc(_entry()))
    link = tmp_path / "tool-approvals.json"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symbolic links are not available here")
    with pytest.raises(ApprovalsError, match="symbolic link"):
        ToolApprovals.load(link)


def test_new_approval_is_validated_and_replaces_the_old_one() -> None:
    first = new_approval(
        server_id="files",
        server_origin=None,
        tool_name="search",
        definition_sha256=DIGEST,
        approved_by="alice",
        now=datetime(2026, 11, 26, 9, 0, tzinfo=UTC),
    )
    assert first.approved_at == "2026-11-26T09:00:00Z"
    updated = ToolApprovals([first]).with_approval(
        new_approval(
            server_id="files",
            server_origin=None,
            tool_name="search",
            definition_sha256="b" * 64,
            approved_by="bob",
            accepted_flags=["role_override"],
        )
    )
    (only,) = list(updated)
    assert only.definition_sha256 == "b" * 64 and only.accepted_flags == ("role_override",)
    with pytest.raises(ApprovalsError):
        new_approval(
            server_id="files",
            server_origin=None,
            tool_name="search",
            definition_sha256="nope",
            approved_by="alice",
        )


# --- bounds -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("schema", "match"),
    [
        ([], "not a JSON object"),
        ({"type": "string"}, '"type": "object"'),
        (
            {"type": "object", "properties": {"x": {"$ref": "https://evil.example/s.json"}}},
            "outside itself",
        ),
        ({"type": "object", "$id": "https://evil.example/"}, "own \\$id"),
        ({"type": "object", "properties": {f"p{i}": {} for i in range(129)}}, "more than 128"),
        ({"type": "object", "description": "x" * 70_000}, "over 64 KiB"),
        ({"type": "object", "default": float("nan")}, "plain JSON"),
    ],
)
def test_out_of_bounds_input_schemas_are_refused(schema: object, match: str) -> None:
    with pytest.raises(SchemaBoundsError, match=match):
        check_input_schema(schema)


def test_deep_schemas_are_refused_and_local_refs_allowed() -> None:
    deep: dict[str, Any] = {"type": "string"}
    for _ in range(20):
        deep = {"type": "object", "properties": {"n": deep}}
    with pytest.raises(SchemaBoundsError, match="deeper"):
        check_input_schema(deep)
    local = {
        "type": "object",
        "$defs": {"s": {"type": "string"}},
        "properties": {"x": {"$ref": "#/$defs/s"}},
    }
    assert check_input_schema(local) == local


def test_model_text_strips_controls_and_caps() -> None:
    assert model_text("a\x00b\x07c\u202ed\nok", 100) == "abcd\nok"
    capped = model_text("x" * 5000, MAX_DESCRIPTION_CHARS)
    assert len(capped) == MAX_DESCRIPTION_CHARS and capped.endswith("…")
    assert model_text(None, 10) == ""


def test_exposed_names() -> None:
    assert exposed_name("files", "search") == "files__search"
    assert exposed_name("files", "a.b") is None
    assert exposed_name("files", "x" * 80) is None


# --- configuration ------------------------------------------------------------------------


def test_http_config_derives_origin_and_hosting() -> None:
    local = McpServerConfig(
        server_id="files", transport="streamable_http", url="http://127.0.0.1:8765/mcp"
    )
    assert (local.origin, local.hosting, local.namespace) == (
        "http://127.0.0.1:8765",
        "local",
        "mcp.files",
    )
    hosted = McpServerConfig(
        server_id="crm", transport="streamable_http", url="https://mcp.example.com/v1"
    )
    assert (hosted.origin, hosted.hosting) == ("https://mcp.example.com", "hosted")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pw@mcp.example.com/",
        "https://mcp.example.com/?key=1",
        "https://mcp.example.com/#x",
    ],
)
def test_http_urls_follow_the_url_rule(url: str) -> None:
    with pytest.raises((ConfigurationError, ValueError)):
        McpServerConfig(server_id="crm", transport="streamable_http", url=url)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"server_id": "Bad", "transport": "in_process"},
        {"server_id": "x", "transport": "stdio"},
        {"server_id": "x", "transport": "streamable_http"},
        {"server_id": "x", "transport": "stdio", "command": "srv", "url": "http://127.0.0.1/"},
        {"server_id": "x", "transport": "in_process", "command": "srv"},
        {"server_id": "x", "transport": "in_process", "surprise": 1},
    ],
)
def test_invalid_configs_are_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises((ConfigurationError, ValueError)):
        McpServerConfig(**kwargs)


def test_stdio_environment_is_an_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_MCP_TEST_ALLOWED", "yes")
    monkeypatch.setenv("FORGE_MCP_TEST_SECRET", "no")
    config = McpServerConfig(
        server_id="files",
        transport="stdio",
        command="files-server",
        env_allowlist=["FORGE_MCP_TEST_ALLOWED"],
    )
    environment = config.stdio_environment()
    assert environment["FORGE_MCP_TEST_ALLOWED"] == "yes"
    assert "FORGE_MCP_TEST_SECRET" not in environment


def test_bearer_token_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    config = McpServerConfig(
        server_id="crm",
        transport="streamable_http",
        url="https://mcp.example.com/",
        bearer_token_env="CRM_TOKEN",
    )
    monkeypatch.delenv("CRM_TOKEN", raising=False)
    with pytest.raises(ConfigurationError) as caught:
        config.bearer_token()
    assert caught.value.context == {"server_id": "crm", "variable": "CRM_TOKEN"}
    monkeypatch.setenv("CRM_TOKEN", "t0ken")
    assert config.bearer_token() == "t0ken"
    assert "t0ken" not in config.model_dump_json()


# --- tool-definition.v1 and mcp-servers.v1 ---------------------------------------------------


def test_definition_snapshot_fixture_matches_schema_reader_and_approval() -> None:
    from forge.tools.mcp.definitions import SCHEMA_PATH as DEFINITION_SCHEMA
    from forge.tools.mcp.definitions import DefinitionStore

    schema = json.loads(DEFINITION_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    store = DefinitionStore(FIXTURES / "tool-definitions")
    approval = ToolApprovals.load(FIXTURES / "tool-approvals.json").get("files", "search")
    assert approval is not None
    path = store.path(approval.definition_sha256)
    jsonschema.validate(json.loads(path.read_bytes()), schema)
    definition = store.load(approval.definition_sha256)
    assert definition is not None and definition["name"] == "search"


def test_definition_store_round_trip_and_tamper_checks(tmp_path: Path) -> None:
    from forge.tools.mcp.definitions import DefinitionError, DefinitionStore
    from forge.tools.mcp.hashing import definition_document, definition_sha256

    store = DefinitionStore(tmp_path / "defs")
    document = definition_document(
        "files", "search", description="Search.", input_schema={"type": "object"}
    )
    digest = store.save(document)
    assert digest == definition_sha256(
        "files", "search", description="Search.", input_schema={"type": "object"}
    )
    assert store.save(document) == digest and store.load(digest) == document
    assert store.load("b" * 64) is None
    with pytest.raises(DefinitionError):
        store.path("../x")
    with pytest.raises(DefinitionError, match="exactly the keys"):
        store.save({"name": "x"})
    path = store.path(digest)
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["definition"]["description"] = "Edited."
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(DefinitionError, match="does not match its hash"):
        store.load(digest)
    with pytest.raises(DefinitionError, match="does not match its hash"):
        store.save(document)
    renamed = store.path("c" * 64)
    renamed.write_text(json.dumps({**tampered, "definition": document}), encoding="utf-8")
    with pytest.raises(DefinitionError, match="does not match its hash"):
        store.load("c" * 64)


def test_rendering_escapes_untrusted_text_and_diffs_by_field() -> None:
    from forge.tools.mcp.definitions import render_definition, render_diff, terminal_safe
    from forge.tools.mcp.hashing import definition_document

    assert terminal_safe("a\x1b[2Jb\u202ec\td\n") == "a\\u001b[2Jb\\u202ec\td\n"
    old = definition_document("files", "s", description="One.", input_schema={"type": "object"})
    new = definition_document(
        "files",
        "s",
        description="Two.",
        input_schema={"type": "object"},
        annotations={"readOnlyHint": False},
    )
    diff = render_diff(old, new)
    assert "description: CHANGED" in diff and "inputSchema: unchanged" in diff
    assert "annotations: CHANGED" in diff and any(line.strip() == "-One." for line in diff)
    assert render_diff(None, new)[0].startswith(
        "The previously approved definition was not recorded"
    )
    assert "outputSchema:" in render_definition(new) and "    (none)" in render_definition(new)


def test_servers_fixture_matches_schema_and_reader() -> None:
    import tomllib

    from forge.tools.mcp.config import SERVERS_SCHEMA_PATH, McpServersFile

    schema = json.loads(SERVERS_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    example = FIXTURES / "mcp-servers.toml"
    jsonschema.validate(tomllib.loads(example.read_text(encoding="utf-8")), schema)
    servers_file, _ = McpServersFile.load(example)
    crm = servers_file.server("crm")
    assert (crm.origin, crm.hosting, crm.bearer_token_env) == (
        "https://mcp.crm.example.com",
        "hosted",
        "CRM_MCP_TOKEN",
    )


@pytest.mark.parametrize(
    "bad",
    [
        {
            "schema_version": "mcp-servers.v1",
            "servers": [{"server_id": "x", "transport": "in_process"}],
        },
        {"schema_version": "mcp-servers.v1", "servers": [{"server_id": "x", "transport": "stdio"}]},
        {
            "schema_version": "mcp-servers.v1",
            "servers": [
                {"server_id": "x", "transport": "streamable_http", "url": "https://u:p@h.example/"}
            ],
        },
        {
            "schema_version": "mcp-servers.v1",
            "servers": [
                {"server_id": "x", "transport": "stdio", "command": "c", "url": "http://127.0.0.1/"}
            ],
        },
        {"schema_version": "mcp-servers.v1", "servers": [], "extra": True},
    ],
)
def test_servers_schema_and_reader_agree_on_rejections(bad: dict[str, Any]) -> None:
    from pydantic import ValidationError

    from forge.tools.mcp.config import SERVERS_SCHEMA_PATH, McpServersFile

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, json.loads(SERVERS_SCHEMA_PATH.read_text(encoding="utf-8")))
    with pytest.raises((ValidationError, ConfigurationError, ValueError)):
        McpServersFile.model_validate(bad)
