"""Rich rendering of the event stream for the CLI.

Turns the orchestrator's :class:`~forge.observability.events.Event` stream into a
readable, colorized live trace so developers can *see* routing decisions, tool
calls, and cost as a run unfolds. Output uses color but no emoji.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from rich.console import Console

from forge.observability.events import Event, EventType


def make_event_renderer(console: Console):  # type: ignore[no-untyped-def]
    """Return an event handler that prints a concise live trace to ``console``."""

    def render(event: Event) -> None:
        data = event.data
        if event.type is EventType.RUN_STARTED:
            console.print(f"[bold cyan]run started[/] [dim]({data.get('mode')})[/]")
        elif event.type is EventType.PLAN_CREATED:
            subtasks = data.get("subtasks", [])
            console.print(f"[bold]plan:[/] {len(subtasks)} subtask(s)")
            for i, subtask in enumerate(subtasks, start=1):
                console.print(f"   [dim]{i}.[/] {subtask}")
        elif event.type is EventType.AGENT_STARTED:
            console.print(f"[cyan]{event.agent}[/] started")
        elif event.type is EventType.AGENT_FINISHED:
            console.print(f"[green]{event.agent} finished[/] [dim](steps={data.get('steps')})[/]")
        elif event.type is EventType.AGENT_FAILED:
            console.print(f"[red]{event.agent} failed[/] [dim]({data.get('reason')})[/]")
        elif event.type is EventType.MODEL_ROUTED:
            console.print(f"   [magenta]-> {data.get('model')}[/] [dim]{data.get('reason')}[/]")
        elif event.type is EventType.MODEL_CALL_FINISHED:
            console.print(
                f"   [dim]{data.get('model')}: {data.get('tokens')} tok / "
                f"${data.get('cost_usd', 0):.4f}[/]"
            )
        elif event.type is EventType.MODEL_CALL_FAILED:
            console.print(f"   [red]model {data.get('model')} failed: {data.get('error')}[/]")
        elif event.type is EventType.TOOL_CALL_STARTED:
            console.print(f"   [yellow]tool {data.get('tool')}[/]({data.get('arguments')})")
        elif event.type is EventType.TOOL_CALL_FINISHED:
            console.print(f"   [green]tool {data.get('tool')} ok[/]")
        elif event.type is EventType.TOOL_CALL_FAILED:
            console.print(f"   [red]tool {data.get('tool')} failed: {data.get('error')}[/]")
        elif event.type is EventType.BUDGET_WARNING:
            console.print(f"[yellow]budget warning[/] [dim]{data}[/]")
        elif event.type is EventType.BUDGET_EXCEEDED:
            console.print(f"[bold red]budget exceeded[/] [dim]{data}[/]")
        elif event.type is EventType.SECURITY_VIOLATION:
            console.print(f"[bold red]security:[/] {data.get('reason')} [dim]{data}[/]")
        elif event.type is EventType.RUN_FINISHED:
            if data.get("success"):
                console.print(
                    f"[bold green]run finished[/] [dim]"
                    f"{data.get('tokens')} tok / ${data.get('cost_usd', 0):.4f}[/]"
                )
            else:
                console.print(f"[bold red]run failed[/] [dim]{data.get('error')}[/]")

    return render


def make_token_renderer() -> Callable[[Event], None]:
    """Return an event handler that prints streamed tokens to stdout in real time.

    Writes each ``TOKEN_CHUNK`` chunk as it arrives with no trailing newline, then
    a single newline when the stream ends — the classic live-typing terminal feel.
    Uses raw ``sys.stdout`` writes (not rich) so token text is never reinterpreted
    as markup.
    """

    def render(event: Event) -> None:
        if event.type is EventType.TOKEN_CHUNK:
            sys.stdout.write(str(event.data.get("chunk", "")))
            sys.stdout.flush()
        elif event.type is EventType.TOKEN_STREAM_END:
            sys.stdout.write("\n")
            sys.stdout.flush()

    return render
