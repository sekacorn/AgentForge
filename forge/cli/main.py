"""The ``forge`` command-line interface.

Designed for a great out-of-the-box developer experience: it works with zero
configuration (offline echo provider) and automatically uses Claude when
``ANTHROPIC_API_KEY`` is set. Run ``forge --help`` to explore.
"""

from __future__ import annotations

import contextlib
import json as jsonlib
import sys

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


@app.command()
def version() -> None:
    """Print the installed Forge version."""
    console.print(f"Forge (AgentForge) v{__version__}")


if __name__ == "__main__":  # pragma: no cover
    app()
