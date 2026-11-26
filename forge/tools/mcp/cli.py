"""``forge mcp``: review and approve MCP tool definitions. Never a blind yes.

``forge mcp status`` connects to each configured server and shows every tool's
state. ``forge mcp approve`` approves tools, and before each decision shows:

* for a **new** tool, its full definition (description, input and output
  schema, annotations);
* for a **changed** tool, a field-by-field diff of the definition that was
  approved and the one now offered, from the ``tool-definition.v1`` snapshot
  recorded at approval (or, when none was recorded, the full new definition
  and a note saying so);
* the **screening reasons** for any flag.

Rules:

* A flagged tool needs ``--accept-flagged``.
* A changed or flagged tool is confirmed by typing its name, never by a y/n.
  Without a prompt (``--yes``) it needs ``--expect-sha256`` with the hash shown
  in its review, so a script cannot approve a definition nobody looked at.
* ``--all-new`` covers only new, unflagged tools. Changed and flagged tools
  are listed and left for review one at a time.

Every approval writes the definition snapshot, updates ``tool-approvals.json``
and is recorded in the audit log as ``agentforge.mcp.tool_approval``. Server
text is untrusted: it is printed with control and bidirectional characters
escaped, and never through a markup renderer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Final

import typer

from forge.compliance.audit import AuditLogger
from forge.config import ForgeConfig
from forge.exceptions import ForgeError
from forge.tools.mcp.approvals import ToolApprovals
from forge.tools.mcp.config import McpServerConfig, McpServersFile
from forge.tools.mcp.connector import DiscoveredTool, McpConnector, ToolState
from forge.tools.mcp.definitions import (
    DefinitionStore,
    render_definition,
    render_diff,
    terminal_safe,
)

AUDIT_APPROVAL: Final = "agentforge.mcp.tool_approval"

app = typer.Typer(
    name="forge mcp",
    help="Review and approve MCP tool definitions.",
    no_args_is_help=True,
    add_completion=False,
)

ConfigOption = Annotated[
    Path, typer.Option("--config", help="The mcp-servers.toml file (mcp-servers.v1).")
]


def _open_connector(server: McpServerConfig, approvals: ToolApprovals) -> McpConnector:
    """Build the connector for one configured server. Replaced in tests."""
    return McpConnector(server, approvals)


def _say(line: str = "") -> None:
    typer.echo(line)


def _fail(message: str) -> typer.Exit:
    typer.echo(f"Error: {message}", err=True)
    return typer.Exit(code=2)


def _load(config: Path) -> tuple[McpServersFile, Path]:
    try:
        return McpServersFile.load(config)
    except (ForgeError, ValueError) as exc:
        raise _fail(str(exc)) from None


@dataclass
class _Outcome:
    approved: list[str] = field(default_factory=list)
    declined: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)


# --- status ----------------------------------------------------------------------------------


@app.command("status")
def status(
    config: ConfigOption = Path("mcp-servers.toml"),
    server: Annotated[
        list[str] | None, typer.Option("--server", help="Only this server (repeatable).")
    ] = None,
) -> None:
    """Show every tool on each configured server and whether it is offered to models."""
    servers_file, base = _load(config)
    try:
        approvals = ToolApprovals.load(base / servers_file.approvals)
    except ValueError as exc:
        raise _fail(str(exc)) from None

    async def run() -> None:
        for server_config in _selected(servers_file, server):
            async with _open_connector(server_config, approvals) as connector:
                _say(
                    f"{server_config.server_id} ({server_config.origin or server_config.transport})"
                )
                for found in connector.discovered():
                    digest = (found.definition_sha256 or "-")[:12]
                    _say(
                        f"  {terminal_safe(found.exposed_name or found.name):40} "
                        f"{found.state.value:9} {digest}  {terminal_safe(found.reason)}"
                    )

    _run(run())


# --- approve ---------------------------------------------------------------------------------


def _selected(servers_file: McpServersFile, names: list[str] | None) -> list[McpServerConfig]:
    if not names:
        return list(servers_file.servers)
    try:
        return [servers_file.server(name) for name in names]
    except ForgeError as exc:
        raise _fail(f"{exc} ({', '.join(names)})") from None


def _parse_tool(value: str) -> tuple[str, str]:
    server_id, sep, name = value.partition("/")
    if not sep or not server_id or not name:
        raise _fail(f"--tool takes SERVER_ID/TOOL_NAME, got {terminal_safe(value)!r}.")
    return server_id, name


def _run(coroutine: object) -> None:
    try:
        asyncio.run(coroutine)  # type: ignore[arg-type]
    except (ForgeError, ValueError) as exc:
        # ApprovalsError and DefinitionError are ValueErrors; neither repeats file content.
        raise _fail(str(exc)) from None


def _review(
    connector: McpConnector, found: DiscoveredTool, store: DefinitionStore, flags: list[str]
) -> None:
    kind = "CHANGED" if found.state is ToolState.CHANGED else "NEW"
    config = connector.config
    _say("=" * 72)
    _say(f"{kind}: {terminal_safe(found.exposed_name or found.name)}   (server {config.server_id})")
    _say(f"  origin:          {config.origin or config.transport}")
    _say(f"  offered sha256:  {found.definition_sha256}")
    if found.state is ToolState.CHANGED:
        approval = connector.approvals.get(config.server_id, found.name)
        _say(f"  approved sha256: {found.approved_sha256}")
        if approval is not None and approval.server_origin != config.origin:
            _say(f"  ORIGIN CHANGED:  approved {approval.server_origin} -> now {config.origin}")
        previous = store.load(found.approved_sha256) if found.approved_sha256 else None
        _say("")
        for line in render_diff(previous, found.definition()):
            _say(f"  {line}")
    else:
        _say("")
        for line in render_definition(found.definition()):
            _say(f"  {line}")
    _say("")
    if flags:
        _say(f"  FLAGGED by screening: {', '.join(flags)}")
        _say("  Read the description and schemas above for instructions aimed at a model.")
    else:
        _say("  Screening: no injection indicators found.")


def _decide(
    found: DiscoveredTool,
    flags: list[str],
    *,
    yes: bool,
    accept_flagged: bool,
    expect_sha256: str | None,
) -> str:
    """``approve``, ``decline`` or a refusal reason."""
    if flags and not accept_flagged:
        return "flagged; rerun for this tool alone with --accept-flagged after reading the review"
    sensitive = bool(flags) or found.state is ToolState.CHANGED
    name = found.exposed_name or found.name
    if yes:
        if not sensitive:
            return "approve"
        if expect_sha256 is None:
            return (
                "changed or flagged; without a prompt it needs "
                f"--expect-sha256 {found.definition_sha256}"
            )
        if expect_sha256 != found.definition_sha256:
            return "--expect-sha256 does not match the definition now offered"
        return "approve"
    if expect_sha256 is not None and expect_sha256 != found.definition_sha256:
        return "--expect-sha256 does not match the definition now offered"
    if sensitive:
        typed = typer.prompt(
            f"  Type {name} to approve this definition (anything else skips)",
            default="",
            show_default=False,
        )
        return "approve" if typed.strip() == name else "decline"
    return "approve" if typer.confirm(f"  Approve {name}?", default=False) else "decline"


@app.command("approve")
def approve(
    approved_by: Annotated[
        str,
        typer.Option(
            "--approved-by", help="Who is approving (recorded in the file and audit log)."
        ),
    ],
    config: ConfigOption = Path("mcp-servers.toml"),
    tool: Annotated[
        list[str] | None,
        typer.Option("--tool", help="SERVER_ID/TOOL_NAME to review and approve (repeatable)."),
    ] = None,
    all_new: Annotated[
        bool,
        typer.Option(
            "--all-new", help="Every new, unflagged tool. Never covers changed or flagged tools."
        ),
    ] = False,
    server: Annotated[
        list[str] | None, typer.Option("--server", help="With --all-new: only this server.")
    ] = None,
    accept_flagged: Annotated[
        bool,
        typer.Option(
            "--accept-flagged", help="Allow approving a flagged tool (named with --tool)."
        ),
    ] = False,
    expect_sha256: Annotated[
        str | None,
        typer.Option(
            "--expect-sha256", help="The offered definition hash, as shown in its review."
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes", help="No prompts: approve new unflagged tools; others need --expect-sha256."
        ),
    ] = False,
) -> None:
    """Review tools, then approve them. Changed and flagged tools are never approved blind."""
    if not approved_by.strip():
        raise _fail("--approved-by must name who is approving.")
    if bool(tool) == all_new:
        raise _fail("Choose either --tool SERVER_ID/TOOL_NAME (repeatable) or --all-new.")
    if all_new and (accept_flagged or expect_sha256):
        raise _fail("--accept-flagged and --expect-sha256 apply to one tool named with --tool.")
    if expect_sha256 is not None and len(tool or []) != 1:
        raise _fail("--expect-sha256 applies to exactly one --tool.")
    if tool and server:
        raise _fail("--server applies to --all-new; --tool already names the server.")

    servers_file, base = _load(config)
    approvals_path = base / servers_file.approvals
    store = DefinitionStore(base / servers_file.definitions)
    forge_config = ForgeConfig.load()
    audit = AuditLogger(
        forge_config.compliance.audit_path, enabled=forge_config.compliance.audit_enabled
    )
    wanted: dict[str, set[str]] = {}
    for value in tool or []:
        server_id, name = _parse_tool(value)
        wanted.setdefault(server_id, set()).add(name)
    targets = _selected(servers_file, sorted(wanted) if wanted else server)
    outcome = _Outcome()
    try:
        state: dict[str, ToolApprovals] = {"approvals": ToolApprovals.load(approvals_path)}
    except ValueError as exc:
        raise _fail(str(exc)) from None

    async def run() -> None:
        for server_config in targets:
            async with _open_connector(server_config, state["approvals"]) as connector:
                await _approve_on(connector, server_config, wanted, state, outcome, store, audit)

    async def _approve_on(
        connector: McpConnector,
        server_config: McpServerConfig,
        wanted: dict[str, set[str]],
        state: dict[str, ToolApprovals],
        outcome: _Outcome,
        store: DefinitionStore,
        audit: AuditLogger,
    ) -> None:
        found_by_name = {found.name: found for found in connector.discovered()}
        candidates: list[DiscoveredTool] = []
        if all_new:
            for found in found_by_name.values():
                label = f"{server_config.server_id}/{terminal_safe(found.name)}"
                if found.state is ToolState.PENDING:
                    flags = connector.screen(found)
                    if flags:
                        outcome.deferred.append(f"{label} (flagged: {', '.join(flags)})")
                    else:
                        candidates.append(found)
                elif found.state is ToolState.CHANGED:
                    outcome.deferred.append(f"{label} (changed)")
        else:
            for name in sorted(wanted.get(server_config.server_id, set())):
                match = found_by_name.get(name)
                label = f"{server_config.server_id}/{terminal_safe(name)}"
                if match is None:
                    outcome.refused.append(f"{label}: the server does not offer this tool")
                elif match.state is ToolState.APPROVED:
                    _say(f"{label}: already approved ({match.definition_sha256})")
                elif match.state in (ToolState.INVALID, ToolState.RETURNED):
                    outcome.refused.append(
                        f"{label}: {match.state.value}: {terminal_safe(match.reason)}"
                    )
                else:
                    candidates.append(match)
        for found in candidates:
            label = f"{server_config.server_id}/{terminal_safe(found.name)}"
            flags = connector.screen(found)
            _review(connector, found, store, flags)
            decision = _decide(
                found, flags, yes=yes, accept_flagged=accept_flagged, expect_sha256=expect_sha256
            )
            if decision == "decline":
                outcome.declined.append(label)
                _say(f"  Skipped {label}.")
                continue
            if decision != "approve":
                outcome.refused.append(f"{label}: {decision}")
                _say(f"  Not approved: {decision}.")
                continue
            previous = found.approved_sha256 if found.state is ToolState.CHANGED else None
            approval = connector.approve(
                found.name, approved_by=approved_by, accept_flagged=bool(flags)
            )
            store.save(found.definition())
            state["approvals"] = state["approvals"].with_approval(approval)
            state["approvals"].save(approvals_path)
            audit.record(
                AUDIT_APPROVAL,
                actor=approved_by,
                resource=f"{server_config.server_id}/{found.name}",
                outcome="approved",
                server_id=server_config.server_id,
                server_origin=server_config.origin,
                tool=found.name,
                definition_sha256=approval.definition_sha256,
                previous_sha256=previous,
                accepted_flags=list(approval.accepted_flags),
            )
            outcome.approved.append(label)
            _say(f"  Approved {label} ({approval.definition_sha256}).")

    _run(run())
    _say("")
    _say(
        f"Approved {len(outcome.approved)}, skipped {len(outcome.declined)}, "
        f"refused {len(outcome.refused)}, left for individual review {len(outcome.deferred)}."
    )
    for line in outcome.refused:
        _say(f"  refused   {line}")
    for line in outcome.deferred:
        _say(f"  review    {line}  ->  forge mcp approve --tool {line.split(' ')[0]}")
    if outcome.approved:
        _say(f"Commit {servers_file.approvals} and {servers_file.definitions}/ to your repository.")
    if outcome.refused:
        raise typer.Exit(code=2)
