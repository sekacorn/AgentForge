# Quickstart

Working output in under 5 minutes. Pick the path that fits your setup.

---

## Path 1: Zero setup (offline, no API key)

Forge ships with a deterministic Echo provider. No API key, no network, no cost.

### CLI

```bash
pip install agentforge-oss

forge run "Plan a product launch and calculate 15% of 3,400" --verbose
```

The `--verbose` flag streams a live trace so you can watch the supervisor plan,
route each call, run tools in the sandbox, and tally cost in real time.

```bash
# Stream token output as it is generated
forge run "Write a short product tagline" --stream

# See the model registry and pricing
forge models

# Verify the audit log hasn't been tampered with
forge audit
```

### Library

```python
import asyncio
from forge import Orchestrator

async def main() -> None:
    async with Orchestrator() as forge:
        result = await forge.run(
            "Draft a one-line product launch tweet and compute a 15% discount on $2,400",
        )
        print(result.output)
        print(result.usage.format_table())

asyncio.run(main())
```

---

## Path 2: With Anthropic (Claude)

```bash
pip install "agentforge-oss[anthropic]"
export ANTHROPIC_API_KEY="sk-ant-..."
```

```python
import asyncio
from forge import Orchestrator

async def main() -> None:
    async with Orchestrator() as forge:
        result = await forge.run(
            "Research our top 3 competitors, draft a comparison, and compute Q3 growth at 18%",
            mode="supervisor",
        )
        print(result.output)
        print(result.usage.format_table())

asyncio.run(main())
```

Forge auto-detects `ANTHROPIC_API_KEY` and routes to Claude. The usage table
shows tokens and cost per model and per agent.

---

## Path 3: With Ollama (free, local)

```bash
pip install agentforge-oss

# Install and start Ollama (https://ollama.ai)
ollama serve
ollama pull llama3.1:8b
```

```python
import asyncio
from forge import Orchestrator

async def main() -> None:
    async with Orchestrator() as forge:
        result = await forge.run(
            "Summarize the benefits of local LLM inference",
            mode="single",
        )
        print(result.output)

asyncio.run(main())
```

Forge auto-detects a running Ollama server at `localhost:11434`. No API key, no
cost, fully air-gapped.

---

## Adding tools

```python
import asyncio
from forge import Orchestrator, ToolRegistry, calculator, tool

@tool
def fx_convert(amount: float, rate: float) -> float:
    """Convert an amount using an FX rate.

    Args:
        amount: The amount in the source currency.
        rate: The exchange rate to apply.
    """
    return round(amount * rate, 2)

async def main() -> None:
    tools = ToolRegistry([calculator, fx_convert])
    async with Orchestrator() as forge:
        result = await forge.run(
            "Convert 250 USD to EUR at 0.92 and then add a 3 EUR fee",
            mode="single",
            tools=tools,
        )
        print(result.output)

asyncio.run(main())
```

The `@tool` decorator turns any typed Python function into an agent tool. The
JSON schema is generated automatically from your type hints and docstring.

---

## Next steps

- [Configuration](configuration.md) -- forge.toml, environment variables, budget caps
- [Providers guide](../guides/providers.md) -- all five providers in detail
- [Tools guide](../guides/tools.md) -- the `@tool` decorator, built-in tools, sandboxing
- [Governance guide](../guides/governance.md) -- RBAC, policy-as-code, audit logs
