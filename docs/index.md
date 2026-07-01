# Forge

**The open-source multi-agent orchestration platform for teams that ship.**

Build, run, optimize, and govern teams of AI agents for real business workflows --
with cost-awareness, security, and compliance built in from line one.

---

## Install in 30 seconds

```bash
pip install agentforge-oss
```

That's it. Forge runs **fully offline** out of the box using a deterministic echo
provider. No API key, no network, no configuration.

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

Set `ANTHROPIC_API_KEY` to route automatically to Claude, or `OPENAI_API_KEY` for GPT.
Both can be set at once; Forge prefers Anthropic by default (configurable).

[Get started](getting-started/quickstart.md){ .md-button .md-button--primary }
[View on GitHub](https://github.com/sekacorn/AgentForge){ .md-button }

---

## Why Forge?

A single AI agent is a clever assistant. **A coordinated _team_ of agents is a force
multiplier** -- it can plan, divide work, call tools, check itself, and deliver a
finished outcome instead of a suggestion.

But most teams can't put multi-agent systems into production. The demos are
impressive; the operational reality is not. The questions that kill adoption are
always the same:

> _"What is this going to cost? Who can run it? What happens when a tool
> misbehaves? Can we prove what it did for the auditors? Will it leak our data?"_

**Forge is built to answer those questions out of the box.** It is an orchestration
core that treats cost, security, governance, and observability as first-class
features -- not afterthoughts you bolt on before launch.

---

## The force-multiplier thesis

| Without Forge | With Forge |
|---|---|
| One prompt, one answer, no division of labor | A supervisor decomposes the goal and delegates to specialized workers |
| Workers run one at a time | Workers run in parallel -- with a pre-flight budget check before any of them start |
| Every call hits your most expensive model | Intelligent routing sends easy work to cheap models, hard work to frontier models |
| Cost is a surprise on the invoice | Cost is tracked per run, per agent, per model -- with hard budget caps |
| Tools run with full trust | Tools run sandboxed, with side-effecting tools **denied by default** |
| "Trust me, it worked" | A tamper-evident, hash-chained audit trail of every action |
| Prototype you can't ship | A typed, tested core designed for production |

---

## Architecture

```
                     +----------------------------------------------+
                     |                Orchestrator                   |
                     |  access control . sanitization . accounting   |
                     +-------------------+--------------------------+
                                         |  RunContext (per run)
              +--------------------------+---------------------------+
              v                          v                           v
      +---------------+         +-----------------+          +--------------+
      |  Supervisor   | spawns  |   Worker Agents |  call    | Tool Sandbox |
      |  plan>delegate|-------->|  reason <> act  |--------->| allow/deny + |
      |  >synthesize  | (gather)|  (parallel loop)|          |  timeouts    |
      +-------+-------+         +--------+--------+          +--------------+
              |                          |
              |      +------------------+-------------------+
              v      v                                       v
       +----------------+                        +------------------------------------+
       |  Model Router  |  picks model by        |  Model Providers                   |
       | cost / quality |  strategy + budget ---->| Anthropic . OpenAI . Ollama . Echo |
       +-------+--------+                        +------------------------------------+
               | pricing
               v
       +----------------+   cross-cutting, on every step:
       | Model Registry |   Usage/Cost . Event Bus . Audit Log . Redaction
       +----------------+
```

Every layer is swappable:

| Layer | Default | Swap in... |
|---|---|---|
| Provider | Echo (offline), Anthropic, OpenAI, Ollama, Bedrock | Any `ModelProvider` |
| Routing | `balanced` strategy | Your own strategy / `fixed` model |
| Memory | InMemoryVectorStore, SQLiteMemoryStore, PGVectorMemoryStore | Any `Memory` backend |
| Tools | `calculator`, `utc_now` | Any `@tool` function |
| Audit | Hash-chained JSONL | Forward events to your SIEM via the event bus |
