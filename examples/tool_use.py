"""Custom tools + a single agent.

Shows how a plain typed Python function becomes an agent tool via ``@tool``
(its JSON schema is generated from the signature and docstring). Offline, the
echo provider exercises the ``calculator`` path end to end; with a real model,
the agent would also choose ``fx_convert`` when appropriate.

Run:
    python examples/tool_use.py
"""

from __future__ import annotations

import asyncio

from forge import Orchestrator, ToolRegistry, calculator, tool


@tool
def fx_convert(amount: float, rate: float) -> float:
    """Convert an amount of money using an exchange rate.

    Args:
        amount: The amount in the source currency.
        rate: The exchange rate to apply.
    """
    return round(amount * rate, 2)


async def main() -> None:
    tools = ToolRegistry([calculator, fx_convert])
    async with Orchestrator() as forge:
        result = await forge.run(
            "Compute 250 * 0.92 to convert USD to EUR, then state the result",
            mode="single",
            tools=tools,
        )
    print(result.output)
    print()
    print(result.usage.format_table())


if __name__ == "__main__":
    asyncio.run(main())
