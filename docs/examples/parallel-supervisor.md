# Parallel supervisor

Shows a supervisor run with concurrent workers, budget caps, and the usage report.

## How it works

In `supervisor` mode, a supervisor agent decomposes the goal into independent
subtasks and spawns a focused worker agent for each. Workers run concurrently
via `asyncio.gather` in bounded batches controlled by `max_workers`.

A pre-flight budget check estimates worst-case spend before spawning workers.
If a worker fails, it emits `WORKER_FAILED` and returns a partial result -- its
peers are never cancelled.

## Full example

```python
import asyncio
from forge import Orchestrator, ForgeConfig, BudgetConfig

async def main() -> None:
    config = ForgeConfig(
        budget=BudgetConfig(max_workers=3, max_usd_per_run=0.25)
    )
    async with Orchestrator(config) as forge:
        result = await forge.run(
            "Summarize our product, draft a pricing page, and write an FAQ",
            mode="supervisor",
        )
        print(result.output)
        print(result.usage.format_table())

asyncio.run(main())
```

## What you'll see

The usage table breaks down tokens and cost per model and per agent:

```
          Model            Input    Output    Cost
          echo-pro          150        75    $0.0000
          ─────────────────────────────────────────
          Total             150        75    $0.0000
```

With a real provider (Anthropic, OpenAI), the cost column shows actual spend
against the `max_usd_per_run` cap.

## Configuration

| Field | Default | Description |
|---|---|---|
| `max_workers` | `5` | Maximum concurrent workers per supervisor batch |
| `max_usd_per_run` | None | Hard USD cap; the run halts if exceeded |
| `max_tokens_per_run` | None | Hard token cap |
| `max_steps_per_agent` | `12` | Maximum reasoning/acting iterations per agent |

## Run it

```bash
python examples/quickstart.py
```

The quickstart example uses supervisor mode with live event rendering so you can
watch the supervisor plan, delegate, and synthesize in real time.
