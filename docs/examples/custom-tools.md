# Custom tools

Based on `examples/tool_use.py`. Shows how a plain typed Python function becomes
an agent tool via `@tool`.

## The pattern

```python
from forge import tool

@tool
def fx_convert(amount: float, rate: float) -> float:
    """Convert an amount of money using an exchange rate.

    Args:
        amount: The amount in the source currency.
        rate: The exchange rate to apply.
    """
    return round(amount * rate, 2)
```

The decorator reads your type annotations and Google-style `Args:` docstring to
build a JSON schema automatically. The agent sees a tool named `fx_convert` with
two float parameters and a description it can reason about.

## Full example

```python
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

asyncio.run(main())
```

## Run it

```bash
python examples/tool_use.py
```

No API key needed -- the Echo provider exercises the tool path end to end.

## Key points

- **Type all parameters.** The JSON schema is generated from annotations.
- **Write a docstring.** The first line becomes the tool description; `Args:` generates parameter descriptions.
- **Mark side effects.** Tools that touch the network or filesystem should use `@tool(dangerous=True)` so the sandbox gates them.
- **Use ToolRegistry.** Group tools into a registry and pass it to `Orchestrator.run()`.
