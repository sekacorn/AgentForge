"""The ``forge`` command-line interface.

Designed for a great out-of-the-box developer experience: it works with zero
configuration (offline echo provider) and automatically uses Claude when
``ANTHROPIC_API_KEY`` is set. Run ``forge --help`` to explore.
"""

from __future__ import annotations

import asyncio
import contextlib
import json as jsonlib
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from forge import __version__
from forge.cli.console import make_event_renderer, make_token_renderer
from forge.config import ForgeConfig
from forge.exceptions import ForgeError
from forge.models.registry import ModelRegistry
from forge.orchestration.orchestrator import Orchestrator


def _ensure_utf8() -> None:
    """Force UTF-8 on stdout/stderr so rich output renders on Windows consoles.

    The legacy Windows code page (cp1252) cannot encode the box-drawing
    characters rich uses for tables and panels, which would otherwise crash
    rendering. Reconfiguring to UTF-8 is safe and a no-op where already UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            # Best-effort: platform dependent, safe to skip if unsupported.
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8")


_ensure_utf8()

app = typer.Typer(
    name="forge",
    help="Forge — multi-agent orchestration with cost, security and governance built in.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
#: Diagnostics go to stderr, so a document on stdout can be piped.
err_console = Console(stderr=True)


def _show_version(value: bool) -> None:
    """Print the installed version for the eager root CLI option."""
    if value:
        console.print(f"Forge (AgentForge) v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_show_version,
        is_eager=True,
        help="Show the installed Forge version and exit.",
    ),
) -> None:
    """Forge command-line interface."""


@app.command()
def run(
    goal: str = typer.Argument(..., help="The goal to accomplish, in plain language."),
    mode: str = typer.Option(
        "supervisor", "--mode", "-m", help="Execution mode: 'supervisor' or 'single'."
    ),
    model: str | None = typer.Option(
        None, "--model", help="Force a specific model id (bypasses routing)."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Stream a live trace of routing, tools and cost."
    ),
    stream: bool = typer.Option(
        False, "--stream", help="Stream model output token-by-token as it is generated."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit the result as JSON instead of formatted text."
    ),
) -> None:
    """Run a goal through Forge and print the result and cost."""
    config = ForgeConfig.load()
    orchestrator = Orchestrator(config)
    if verbose and not json_output:
        orchestrator.subscribe(make_event_renderer(console))
    if stream and not json_output:
        orchestrator.subscribe(make_token_renderer())

    try:
        result = orchestrator.run_sync(goal, mode=mode, model=model, stream=stream)
    except ForgeError as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    if json_output:
        console.print_json(
            jsonlib.dumps(
                {
                    "run_id": result.run_id,
                    "output": result.output,
                    "usage": result.usage.model_dump(mode="json"),
                }
            )
        )
        return

    console.print(Panel(result.output, title="Result", border_style="green"))
    console.print(result.usage.format_table(), style="dim")


@app.command()
def models() -> None:
    """List the models Forge knows about, with tier and pricing."""
    registry = ModelRegistry()
    table = Table(title="Forge model registry")
    table.add_column("Model", style="bold")
    table.add_column("Provider")
    table.add_column("Tier")
    table.add_column("Context", justify="right")
    table.add_column("$/1M in", justify="right")
    table.add_column("$/1M out", justify="right")
    table.add_column("Tools", justify="center")
    for info in sorted(registry.all(), key=lambda m: (m.provider, m.tier.rank)):
        table.add_row(
            info.name,
            info.provider,
            info.tier.value,
            f"{info.context_window:,}",
            f"${info.input_cost_per_mtok:g}",
            f"${info.output_cost_per_mtok:g}",
            "yes" if info.supports_tools else "no",
        )
    console.print(table)


@app.command()
def audit() -> None:
    """Verify the integrity of the audit log's hash chain."""
    config = ForgeConfig.load()
    orchestrator = Orchestrator(config)
    ok = orchestrator.verify_audit()
    if ok:
        console.print(f"[green]audit log intact[/] [dim]({config.compliance.audit_path})[/]")
    else:
        console.print("[bold red]audit log integrity check FAILED[/]")
        raise typer.Exit(code=1)


providers_app = typer.Typer(help="Describe the model providers Forge is configured with.")
app.add_typer(providers_app, name="providers")


@providers_app.command("inventory")
def providers_inventory(
    declarations: Annotated[
        Path,
        typer.Option(
            "--declarations",
            help="A provider-declarations.v1 file: features in use, vendor-only features, "
            "and optionally models, jurisdiction and fallback, per provider.",
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write the inventory here instead of printing it."),
    ] = None,
    use_registry_models: Annotated[
        bool,
        typer.Option(
            "--use-registry-models",
            help="For a provider whose models are not declared, take them from the model "
            "registry, marked as from registry, not confirmed in use. Without this flag such "
            "a provider is refused.",
        ),
    ] = False,
) -> None:
    """Write a provider-inventory.v1 for the configured providers."""
    from forge.models.inventory import build_inventory, load_declarations, render_inventory

    try:
        declared = load_declarations(declarations)
        providers = Orchestrator.configured_providers(ForgeConfig.load())
        result = build_inventory(providers, declared, use_registry_models=use_registry_models)
    except ForgeError as exc:
        err_console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=2) from exc
    text = render_inventory(result)
    for note in result.notes:
        err_console.print(f"[yellow]note:[/] {note}")
    if output is None:
        # Plain stdout: the document must not pass through console markup.
        sys.stdout.write(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8", newline="\n")
    err_console.print(f"provider inventory written to {output}")


memory_app = typer.Typer(help="Inspect and migrate Forge memory stores.")
app.add_typer(memory_app, name="memory")


async def _open_and_close(store: object) -> None:
    async with store:  # type: ignore[attr-defined]
        pass


@memory_app.command("migrate")
def memory_migrate(
    print_sql: Annotated[
        bool,
        typer.Option(
            "--print-sql",
            help="Print the additive statements instead of running them, for a DBA to apply.",
        ),
    ] = False,
) -> None:
    """Apply the configured memory store's additive schema migration.

    Opening a SQLite or pgvector store runs the same migration automatically;
    this command lets an operator run it deliberately, or print it.
    """
    from forge.memory import build_memory

    try:
        store = build_memory(ForgeConfig.load())
    except ForgeError as exc:
        err_console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=2) from exc
    migration_sql = getattr(store, "migration_sql", None)
    if migration_sql is None:
        err_console.print("The configured memory store is in-memory; there is nothing to migrate.")
        return
    if print_sql:
        # Plain stdout, so the statements can be piped to psql or sqlite3.
        sys.stdout.write("".join(f"{statement};\n" for statement in migration_sql()))
        return
    try:
        asyncio.run(_open_and_close(store))
    except ForgeError as exc:
        err_console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=2) from exc
    err_console.print(
        "Memory store schema is up to date (additive migration; nothing dropped or renamed)."
    )


@app.command()
def version() -> None:
    """Print the installed Forge version."""
    console.print(f"Forge (AgentForge) v{__version__}")


if __name__ == "__main__":  # pragma: no cover
    app()
