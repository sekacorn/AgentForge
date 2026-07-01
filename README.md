<div align="center">

# Forge

### The open-source multi-agent orchestration platform for teams that ship.

**Build, run, optimize, and govern teams of AI agents for real business workflows —
with cost-awareness, security, and compliance built in from line one.**

[![CI](https://github.com/sekacorn/AgentForge/actions/workflows/ci.yml/badge.svg)](https://github.com/sekacorn/AgentForge/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agentforge-oss.svg)](https://pypi.org/project/agentforge-oss/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Typed](https://img.shields.io/badge/typed-mypy%20strict-blue.svg)](pyproject.toml)
[![Status: Beta](https://img.shields.io/badge/status-beta-orange.svg)](#whats-shipped-v050)

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
    # Zero config. Runs fully offline with the deterministic echo provider, and
    # automatically uses Claude or GPT when an API key is present in the environment.
    async with Orchestrator() as forge:
        result = await forge.run(
            "Research our top 3 competitors, draft a comparison, and compute Q3 growth at 18%",
            mode="supervisor",   # spawns parallel workers, one per subtask
        )
        print(result.output)
        print(result.usage.format_table())   # tokens + cost, per model and per agent

asyncio.run(main())
```

---

## What's shipped (v0.5.0)

An honest snapshot of what works today versus what is on the way. Everything marked
**Shipped** is implemented, typed, and covered by the test suite.

| Feature | Status |
|---|---|
| Multi-agent orchestration (supervisor + parallel workers) | Shipped |
| Intelligent model routing (`cost_optimized`, `quality_first`, `balanced`, `fixed`) | Shipped |
| Anthropic provider (Claude Haiku 4.5, Sonnet 4.6, Opus 4.8, Fable 5) | Shipped |
| OpenAI provider (gpt-4o-mini, gpt-4o, gpt-4.1, o3) | Shipped |
| Offline deterministic provider (zero config, no API key) | Shipped |
| Ollama provider (local models, zero cost, no API key, auto-detected) | Shipped |
| Amazon Bedrock provider (Claude/Llama/Mistral via Converse, in-account/GovCloud) | Shipped |
| Pre-flight + per-step budget caps | Shipped |
| Tool sandboxing (allowlist/denylist, timeouts, dangerous-denied-by-default) | Shipped |
| RBAC (admin / operator / developer / viewer) | Shipped |
| Prompt-injection heuristics + input sanitization | Shipped |
| SHA-256 hash-chained tamper-evident audit log | Shipped |
| PII redaction (emails, cards, SSNs, IPs, phones) | Shipped |
| Event bus (25 lifecycle event types) | Shipped |
| Streaming token output through the event bus (`stream=True`) | Shipped |
| Per-run cost reporting (tokens + USD, per model, per agent) | Shipped |
| OpenTelemetry export (traces + metrics: console or OTLP to Jaeger/Grafana/Datadog) | Shipped |
| Conversation memory + in-memory RAG vector store | Shipped |
| Durable memory backend (SQLite, persistent RAG, no vector extension) | Shipped |
| CLI (`forge run`, `forge models`, `forge audit`) | Shipped |
| 115 tests, mypy strict, ruff clean, CI on 3.11 / 3.12 / 3.13 | Shipped |
| Durable memory backend (pgvector, PostgreSQL + IVFFlat ANN, asyncpg) | Shipped |
| Durable memory backends (Redis) | Planned |
| Vertex / other cloud providers | Planned |
| Policy-as-code governance (deny / approve / log rules, human-in-the-loop gates) | Shipped |
| Hosted SaaS control plane (TypeScript / Next.js) | Future |

---

## The force-multiplier thesis

| Without Forge | With Forge |
|---|---|
| One prompt, one answer, no division of labor | A supervisor decomposes the goal and delegates to specialized workers |
| Workers run one at a time | Workers run in parallel — with a pre-flight budget check before any of them start |
| Every call hits your most expensive model | Intelligent routing sends easy work to cheap models, hard work to frontier models |
| Cost is a surprise on the invoice | Cost is tracked per run, per agent, per model — with hard budget caps |
| Tools run with full trust | Tools run sandboxed, with side-effecting tools **denied by default** |
| "Trust me, it worked" | A tamper-evident, hash-chained audit trail of every action |
| Prototype you can't ship | A typed, tested core designed for production |

---

## Key features

### Multi-agent orchestration
- **Supervisor + dynamic workers.** A supervisor breaks a goal into independent
  subtasks and spawns a focused worker agent for each, then synthesizes the result.
- **True parallelism.** Workers run concurrently via `asyncio.gather` in bounded
  batches. A configurable `max_workers` cap (default 5) keeps fan-out under control,
  so the supervisor never blocks on one worker while others could be progressing.
- **Graceful failure isolation.** A crashing worker emits `WORKER_FAILED`, records the
  error to the audit log, and returns a partial result — its peers are never
  cancelled, and the run completes with everything that succeeded.
- **Real agentic loop.** Workers reason, call tools, observe results, and iterate
  until done — with a hard step budget so they never spin forever.

### Intelligent routing & cost optimization
- **Capability/price-aware router** with `cost_optimized`, `quality_first`,
  `balanced`, and `fixed` strategies.
- **One pricing source of truth.** The model registry knows real list pricing across
  all providers (Claude Haiku 4.5, Sonnet 4.6, Opus 4.8, Fable 5; GPT-4o-mini, GPT-4o,
  GPT-4.1, o3) and computes spend consistently.
- **Budgets that bite — twice.** A pessimistic *pre-flight* check estimates the
  worst-case spend of a worker batch and refuses to start it if that would blow the
  remaining budget. A precise, real-time check then fires after every model call.
  Together they bound both over-spend and wasted API calls.

### Tools, memory & RAG — extensible by design
- **`@tool` decorator** turns any typed Python function into an agent tool, with
  JSON-Schema generated automatically from your type hints and docstring.
- **Pluggable memory.** Short-term conversation memory plus a dependency-free
  in-memory vector store for RAG — swap in any backend behind one tiny interface.
  Three durable backends ship in the box: in-memory (default), SQLite (zero
  extension), and pgvector (PostgreSQL + IVFFlat ANN for production-scale RAG).
- **Provider-agnostic core.** Anthropic (Claude), OpenAI (GPT / o-series), Amazon
  Bedrock (Converse API), and Ollama (local models, zero cost) ship in the box
  alongside a deterministic offline echo provider; add any provider by implementing
  one method.

### Security from the start
- **Tool sandboxing** with allowlists/denylists, per-tool timeouts, and
  **dangerous (network/filesystem) tools denied unless explicitly allowed**.
- **Prompt-injection heuristics** and input normalization on untrusted goals.
- **RBAC** — map your IdP groups onto roles (`admin`, `operator`, `developer`,
  `viewer`) and gate who can run agents or use dangerous tools.

### Compliance & governance
- **Tamper-evident audit log.** Every model call, tool call, plan, and decision is
  written to an append-only, **SHA-256 hash-chained** JSONL trail. Edits break the
  chain — and `forge audit` detects it.
- **PII redaction** of logs and audit records (emails, cards, SSNs, IPs, phones).
- **Data-residency & retention** hints recorded on every entry for GDPR/SOC 2 stories.
- **Policy-as-code governance.** Define rules that evaluate before any tool call
  executes. Three actions: `deny` (hard block), `approve` (human-in-the-loop gate),
  or `log` (audit without blocking). Rules are plain Python -- composable, testable,
  version-controllable.

```python
from forge import PolicyRule, PolicySet

policy = PolicySet()
policy.add(PolicyRule(
    name="require-approval-for-network",
    description="Any network call requires human sign-off",
    tool_names=["http_get"],
    condition=lambda name, args: True,
    action="approve",
    approver=my_approval_webhook,  # async fn returning bool
))
```

### Observability built in
- A structured **event bus** emits **25 distinct lifecycle event types** — run, agent,
  and worker start/finish (including `WORKER_STARTED` and `WORKER_FAILED` for parallel
  execution), model routing and calls, tool calls, budget thresholds, and security
  violations — so any subscriber (console, audit, metrics) sees the same stream.
- **Token streaming.** Run with `stream=True` and the platform emits
  `TOKEN_STREAM_START` / `TOKEN_CHUNK` / `TOKEN_STREAM_END` events — each tagged with
  the agent that produced it, so you can render live output even across parallel
  workers. `forge run "..." --stream` gives the classic live-typing terminal feel.
- **OpenTelemetry export.** Every run becomes a tree of spans (`forge.run` →
  `forge.agent` → `forge.model_call` → `forge.tool_call`) exportable to any
  OTel-compatible backend — Jaeger, Grafana, Datadog, Honeycomb, New Relic. Console
  exporter by default (zero infra); set an OTLP endpoint for production.
- **Structured logging** (human or JSON) and a per-run **usage/cost report** broken
  down per model and per agent.

---

## Install

```bash
pip install agentforge-oss                       # core (works offline, zero config)
pip install "agentforge-oss[anthropic]"          # + Claude provider
pip install "agentforge-oss[openai]"             # + OpenAI / GPT provider
pip install "agentforge-oss[anthropic,openai]"   # both real providers
pip install "agentforge-oss[bedrock]"            # + Amazon Bedrock provider (boto3)
pip install "agentforge-oss[pgvector]"           # + PostgreSQL RAG (asyncpg + pgvector)
pip install "agentforge-oss[all,dev]"            # everything + test/lint tooling
```

> **Note:** The PyPI package is `agentforge-oss` — so `pip install agentforge-oss`.
> The import is still `import forge`. This follows the same convention as
> `pip install Pillow` then `import PIL`.

> **Model names:** Forge uses registry aliases (e.g. `claude-sonnet-4-6`, `gpt-4o`) that map to provider model IDs in `forge/models/registry.py`. Aliases can be updated as provider IDs change without touching your code. Run `forge models` to see the full registry with current aliases, tiers, and pricing.

> Forge runs **fully offline** out of the box using a deterministic echo provider, so
> you can explore the whole platform — routing, tools, supervision, audit — without an
> API key. Set `ANTHROPIC_API_KEY` to route automatically to Claude, or `OPENAI_API_KEY`
> to route to GPT. Both keys can be set at once; Forge prefers Anthropic by default
> (configurable).

> **Ollama support is built in** — no extra install needed (it uses `httpx`, already a
> core dependency, so there is no `[ollama]` extra). Just run Ollama locally and Forge
> auto-detects it (or set `OLLAMA_BASE_URL` for a custom/remote server):

```bash
ollama serve
ollama pull llama3.1:8b
```

---

## Quickstart (CLI)

```bash
# Works with no API key — uses the offline provider.
forge run "Plan a product launch and calculate 15% of 3,400" --verbose

# Stream model output token-by-token as it is generated.
forge run "Write a short product tagline" --stream

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

### Parallel supervisor — workers run concurrently, one per subtask

```python
import asyncio
from forge import Orchestrator, ForgeConfig, BudgetConfig

async def main() -> None:
    config = ForgeConfig(budget=BudgetConfig(max_workers=3, max_usd_per_run=0.25))
    async with Orchestrator(config) as forge:
        result = await forge.run(
            "Summarize our product, draft a pricing page, and write an FAQ",
            mode="supervisor",
        )
        print(result.output)
        print(result.usage.format_table())

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

### Durable RAG with PostgreSQL + pgvector

```python
import asyncio
from forge import PGVectorMemoryStore

async def main() -> None:
    dsn = "postgresql://forge:forge@localhost:5432/forge"
    async with PGVectorMemoryStore(dsn) as store:
        await store.add("Forge routes cheap tasks to small models to save cost.")
        await store.add("The audit log is hash-chained and tamper-evident.")
        hits = await store.search("how does Forge keep costs down?", k=1)
        print(hits[0].text)   # -> the cost-routing fact

asyncio.run(main())
```

Requires `pip install "agentforge-oss[pgvector]"` and a PostgreSQL instance with the
`vector` extension. Set `FORGE_MEMORY_BACKEND=pgvector` and `FORGE_PGVECTOR_DSN` in
your environment to wire it into the orchestrator automatically.

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
      │  Supervisor   │ spawns  │   Worker Agents │  call    │ Tool Sandbox │
      │  plan→delegate│────────▶│  reason ↔ act   │─────────▶│ allow/deny + │
      │  →synthesize  │ (gather)│  (parallel loop)│          │  timeouts    │
      └───────┬───────┘         └────────┬────────┘          └──────────────┘
              │                          │
              │      ┌───────────────────┴───────────────┐
              ▼      ▼                                    ▼
       ┌────────────────┐                        ┌────────────────────────────────────┐
       │  Model Router  │  picks model by        │  Model Providers                   │
       │ cost / quality │  strategy + budget ───▶│ Anthropic · OpenAI · Ollama · Bedrock · Echo │
       └───────┬────────┘                        └────────────────────────────────────┘
               │ pricing
               ▼
       ┌────────────────┐   cross-cutting, on every step:
       │ Model Registry │   Usage/Cost · Event Bus · Audit Log · Redaction
       └────────────────┘
```

Every layer is swappable:

| Layer | Default | Swap in… |
|---|---|---|
| Provider | Echo (offline), Anthropic, OpenAI, Ollama, Bedrock | Any `ModelProvider` (Vertex, …) |
| Routing | `balanced` strategy | Your own strategy / `fixed` model |
| Memory | InMemoryVectorStore (default), SQLiteMemoryStore, PGVectorMemoryStore | Any `Memory` backend (Redis, ...) |
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

Common environment variables: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`FORGE_DEFAULT_MODEL`, `FORGE_ROUTING_STRATEGY`, `FORGE_MAX_USD_PER_RUN`,
`FORGE_LOG_LEVEL`, `FORGE_JSON_LOGS`, `FORGE_AUDIT_ENABLED`, `FORGE_REDACT_PII`. See
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

The entire 115-test suite runs offline against the deterministic provider — fast,
hermetic, and free. CI runs the same checks (ruff, ruff format, mypy strict, pytest)
on Python 3.11, 3.12, and 3.13.

---

## Contributing

Contributions are very welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Please keep
the core typed (`mypy --strict`) and tested.

### Good first contributions

- **New model provider** (Google Vertex, Gemini, Cohere, Mistral) — implement one method. See forge/models/providers/anthropic.py as the reference.
- **New built-in tool** (web search, file read, database query).
- **Durable memory backend** (Redis) — SQLite and pgvector ship in the box already; Redis is the next backend behind the same Memory interface.
- **Routing strategy** (a custom cost/quality tradeoff).
- **Example workflows** that show Forge solving a real business problem.

## License

[MIT](LICENSE) — free for commercial and private use. Build something that multiplies
your team.
