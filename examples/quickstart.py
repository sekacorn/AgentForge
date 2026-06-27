"""Forge quickstart — a multi-agent run, fully offline.

A supervisor decomposes the goal, delegates to workers, runs tools in the
sandbox, and reports cost. No API key required (uses the deterministic echo
provider); set ANTHROPIC_API_KEY to route to Claude automatically.

Run:
    python examples/quickstart.py
"""

from __future__ import annotations

import asyncio

from rich.console import Console

from forge import Orchestrator, ToolRegistry, calculator, utc_now
from forge.cli.console import make_event_renderer


async def main() -> None:
    console = Console()
    tools = ToolRegistry([calculator, utc_now])

    async with Orchestrator() as forge:
        # Watch routing, tool calls and cost stream live.
        forge.subscribe(make_event_renderer(console))

        result = await forge.run(
            "Draft a one-line product launch tweet and compute a 15% discount on $2,400",
            tools=tools,
        )

    console.rule("Result")
    console.print(result.output)
    console.rule("Cost")
    console.print(result.usage.format_table())


if __name__ == "__main__":
    asyncio.run(main())
