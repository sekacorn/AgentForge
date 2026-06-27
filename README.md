<div align="center">

# 🔨 Forge

### The open-source multi-agent orchestration platform for teams that ship.

**Build, run, optimize, and govern teams of AI agents for real business workflows —
with cost-awareness, security, and compliance built in from line one.**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Typed](https://img.shields.io/badge/typed-mypy%20strict-blue.svg)](pyproject.toml)
[![Status: Beta](https://img.shields.io/badge/status-beta-orange.svg)](#roadmap)

</div>

---

## Why Forge?

A single AI agent is a clever assistant. **A coordinated _team_ of agents is a force
multiplier** — it can plan, divide work, call tools, check itself, and deliver a
finished outcome instead of a suggestion.

But most teams can't put multi-agent systems into production. The demos are
impressive; the operational reality is not. The questions that kill adoption are
always the same:

> _"What is this going to cost? Who can run it? What happens when a tool
> misbehaves? Can we prove what it did for the auditors? Will it leak our data?"_

**Forge is built to answer those questions out of the box.** It is an orchestration
core that treats cost, security, governance, and observability as first-class
features — not afterthoughts you bolt on before launch.

```python
import asyncio
from forge import Orchestrator

async def main() -> None:
    # Zero config. Runs fully offline with the deterministic echo provider,
    # and automatically uses Claude when ANTHROPIC_API_KEY is set.
    async with Orchestrator() as forge:
        result = await forge.run(
            "Draft an outreach email to a prospect and compute a 12% discount on $4,200"
        )
        print(result.output)
        print(result.usage.format_table())   # tokens + dollars, per model and per agent

asyncio.run(main())
```

---

## The force-multiplier thesis

| Without Forge | With Forge |
|---|---|
| One prompt, one answer, no division of labor | A supervisor decomposes the goal and delegates to specialized workers |
| Every call hits your most expensive model | Intelligent routing sends easy work to cheap models, hard work to frontier models |
| Cost is a surprise on the invoice | Cost is tracked per run, per agent, per model — with hard budget caps |
| Tools run with full trust | Tools run sandboxed, with side-effecting tools **denied by default** |
| "Trust me, it worked" | A tamper-evident, hash-chained audit trail of every action |
| Prototype you can't ship | A typed, tested core designed for production |

---

## Key features

### 🧭 Multi-agent orchestration
- **Supervisor + dynamic workers.** A supervisor breaks a goal into independent
  subtasks and spawns a focused worker agent for each, then synthesizes the result.
- **Real agentic loop.** Workers reason, call tools, observe results, and iterate
  until done — with a hard step budget so they never spin forever.

### 💸 Intelligent routing & cost optimization
- **Capability/price-aware router** with `cost_optimized`, `quality_first`,
  `balanced`, and `fixed` strategies.
- **One pricing source of truth.** The model registry knows real list pricing
  (Claude Opus 4.8, Sonnet 4.6, Haiku 4.5, Fable 5) and computes spend consistently.
- **Budgets that bite.** Per-run USD and token caps halt a runaway before the next
  expensive call.

### 🧰 Tools, memory & RAG — extensible by design
- **`@tool` decorator** turns any typed Python function into an agent tool, with
  JSON-Schema generated automatically from your type hints and docstring.
- **Pluggable memory.** Short-term conversation memory plus a dependency-free
  in-memory vector store for RAG — swap in any backend behind one tiny interface.
- **Provider-agnostic core.** Anthropic (Claude) and a deterministic offline
  provider ship in the box; add any provider by implementing one method.

### 🔒 Security from the start
- **Tool sandboxing** with allowlists/denylists, per-tool timeouts, and
  **dangerous (network/filesystem) tools denied unless explicitly allowed**.
- **Prompt-injection heuristics** and input normalization on untrusted goals.
- **RBAC** — map your IdP groups onto roles and gate who can run agents or use
  dangerous tools.

### 📜 Compliance & governance
- **Tamper-evident audit log.** Every model call, tool call, plan, and decision is
  written to an append-only, **SHA-256 hash-chained** JSONL trail. Edits break the
  chain — and `forge audit` detects it.
- **PII redaction** of logs and audit records (emails, cards, SSNs, IPs, phones).
- **Data-residency & retention** hints recorded on every entry for GDPR/SOC 2 stories.

### 🔭 Observability built in
- A structured **event bus** emits every lifecycle moment (routing, model calls,
  tool calls, budget thresholds, security violations).
- **Structured logging** (human or JSON) and a per-run **usage/cost report**.

---

## Install

```bash
pip install agentforge            # core (works offline, zero config)
pip install "agentforge[anthropic]"   # + Claude provider
pip install "agentforge[all,dev]"     # everything + test/lint tooling
```

> Forge runs **fully offline** out of the box using a deterministic provider, so you
> can explore the whole platform — routing, tools, supervision, audit — without an
> API key. Set `ANTHROPIC_API_KEY` to route to Claude automatically.

---

## Quickstart (CLI)

```bash
# Works with no API key — uses the offline provider.
forge run "Plan a product launch and calculate 15% of 3,400" --verbose

# See the model registry and pricing the router reasons over.
forge models

# Verify the audit log hasn't been tampered with.
forge audit
```

`--verbose` streams a live trace so you can watch the supervisor plan, route each
call, run tools in the sandbox, and tally cost in real time.

---

## Quickstart (library)

### A custom tool + a single agent

```python
import asyncio
from forge import Orchestrator, ToolRegistry, tool, calculator

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

### Retrieval-augmented generation (RAG), offline

```python
import asyncio
from forge import InMemoryVectorStore

async def main() -> None:
    store = InMemoryVectorStore()
    await store.add("Forge routes cheap tasks to small models to save cost.")
    await store.add("The audit log is hash-chained and tamper-evident.")
    hits = await store.search("how does Forge keep costs down?", k=1)
    print(hits[0].text)   # -> the cost-routing fact

asyncio.run(main())
```

See [`examples/`](examples/) for runnable end-to-end scripts, including enterprise
governance (RBAC + budgets + audit verification).

---

## Architecture

```
                         ┌──────────────────────────────────────────────┐
                         │                Orchestrator                  │
                         │  access control · sanitization · accounting  │
                         └───────────────┬──────────────────────────────┘
                                         │  RunContext (per run)
              ┌──────────────────────────┼───────────────────────────┐
              ▼                          ▼                           ▼
      ┌───────────────┐         ┌─────────────────┐          ┌──────────────┐
      │  Supervisor   │ spawns  │   Worker Agent  │  calls   │ Tool Sandbox │
      │  plan→delegate│────────▶│  reason ↔ act   │─────────▶│ allow/deny + │
      │  →synthesize  │         │   (agentic loop)│          │  timeouts    │
      └───────┬───────┘         └────────┬────────┘          └──────────────┘
              │                          │
              │      ┌───────────────────┴───────────────┐
              ▼      ▼                                    ▼
       ┌────────────────┐                        ┌──────────────────┐
       │  Model Router  │  picks model by        │  Model Providers │
       │ cost / quality │  strategy + budget ───▶│ Anthropic · Echo │
       └───────┬────────┘                        └──────────────────┘
               │ pricing
               ▼
       ┌────────────────┐   cross-cutting, on every step:
       │ Model Registry │   Usage/Cost · Event Bus · Audit Log · Redaction
       └────────────────┘
```

Every layer is swappable:

| Layer | Default | Swap in… |
|---|---|---|
| Provider | Echo (offline), Anthropic | Any `ModelProvider` (OpenAI, local, Bedrock, …) |
| Routing | `balanced` strategy | Your own strategy / `fixed` model |
| Memory | In-memory vector store | Any `Memory` backend (pgvector, Pinecone, …) |
| Tools | `calculator`, `utc_now` | Any `@tool` function |
| Audit | Hash-chained JSONL | Forward events to your SIEM via the event bus |

---

## Enterprise use cases

Forge is a force multiplier wherever a workflow is **multi-step, judgment-heavy, and
needs a paper trail**:

- **Revenue & RevOps** — enrich a lead, draft tailored outreach, compute discounts
  within policy, and log every step for the deal record.
- **Customer support** — triage a ticket, retrieve the right KB articles (RAG),
  draft a grounded reply, and escalate by policy.
- **Finance & operations** — reconcile figures with a sandboxed calculator, summarize
  variances, and produce an auditable trail for controllers.
- **Compliance & risk** — run document review where _every_ action is recorded in a
  tamper-evident log, with PII redacted and access role-gated.
- **Engineering** — fan out research across a codebase with a supervisor, keeping
  cheap models on grunt work and frontier models on the hard reasoning.

The common thread: Forge lets you **say yes to production** because the governance
questions already have answers.

---

## Security & compliance posture

- **Least privilege by default.** Dangerous tools (network egress, filesystem) are
  denied unless you explicitly allowlist them.
- **Defense in depth.** Input sanitization + prompt-injection heuristics sit in front
  of every untrusted goal; tool execution is bounded by timeouts.
- **Provable history.** The audit log is append-only and hash-chained; `forge audit`
  (or `Orchestrator.verify_audit()`) detects any tampering.
- **Privacy aware.** PII redaction runs before anything is written to logs or audit.
- **Access control.** RBAC gates sensitive operations; map roles to your IdP groups.

> Forge gives you strong application-level controls. It is not a substitute for OS-
> level isolation when executing untrusted code — for that, run tools in a container
> or microVM behind the same `ToolSandbox` interface. The design makes that a drop-in.

---

## Configuration

Configuration layers (lowest to highest priority): **defaults → `forge.toml` →
environment**.

```toml
# forge.toml
[routing]
strategy = "balanced"          # cost_optimized | quality_first | balanced | fixed
default_model = "claude-opus-4-8"

[budget]
max_usd_per_run = 0.50
max_steps_per_agent = 12
max_workers = 6

[security]
detect_prompt_injection = true
tool_timeout_seconds = 30
# allow_tools = ["calculator", "http_get"]   # uncomment to permit a dangerous tool

[compliance]
audit_enabled = true
redact_pii = true
data_region = "eu-west-1"
```

Common environment variables: `ANTHROPIC_API_KEY`, `FORGE_DEFAULT_MODEL`,
`FORGE_ROUTING_STRATEGY`, `FORGE_MAX_USD_PER_RUN`, `FORGE_LOG_LEVEL`,
`FORGE_JSON_LOGS`, `FORGE_AUDIT_ENABLED`, `FORGE_REDACT_PII`. See
[`.env.example`](.env.example).

---

## Development

```bash
git clone https://github.com/sekacorn/AgentForge.git
cd AgentForge
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[all,dev]"

pytest            # run the test suite (offline, no API key needed)
ruff check .      # lint
mypy forge        # strict type-check
```

The entire test suite runs offline against the deterministic provider — fast,
hermetic, and free.

---

## Roadmap

- [ ] Streaming token output through the event bus
- [ ] First-party OpenAI / Bedrock / local (Ollama) providers
- [ ] Durable memory backends (pgvector, Redis, object storage)
- [ ] Parallel worker execution & inter-agent messaging
- [ ] OpenTelemetry export for traces and metrics
- [ ] Policy-as-code for tool governance
- [ ] Hosted SaaS control plane (TypeScript / Next.js) — Python core first

---

## Contributing

Contributions are very welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Good first
areas: new providers, new tools, memory backends, and routing strategies. Please keep
the core typed (`mypy --strict`) and tested.

## License

[MIT](LICENSE) — free for commercial and private use. Build something that multiplies
your team.
