# Observability

Forge provides three observability layers: a structured event bus, OpenTelemetry
export, and per-run cost reporting. Together they give you full visibility into
what agents are doing, how much it costs, and where time is spent.

---

## Event bus

A lightweight, in-process event bus emits **25 distinct lifecycle event types**.
Subscribers -- console renderers, audit loggers, metrics exporters -- observe the
same stream without producers knowing who is listening.

### All 25 event types

| Event type | When it fires |
|---|---|
| `RUN_STARTED` | A new orchestrator run begins |
| `RUN_FINISHED` | A run completes |
| `PLAN_CREATED` | A supervisor creates a task plan |
| `AGENT_STARTED` | An agent begins its loop |
| `AGENT_FINISHED` | An agent completes successfully |
| `AGENT_FAILED` | An agent fails with an error |
| `WORKER_STARTED` | A parallel worker is spawned |
| `WORKER_FAILED` | A parallel worker fails (peers continue) |
| `MODEL_CALL_STARTED` | A model API call begins |
| `MODEL_CALL_FINISHED` | A model API call returns |
| `MODEL_CALL_FAILED` | A model API call errors |
| `MODEL_ROUTED` | The router selects a model |
| `TOKEN_STREAM_START` | Token streaming begins for an agent |
| `TOKEN_CHUNK` | A chunk of streamed tokens arrives |
| `TOKEN_STREAM_END` | Token streaming completes |
| `TOOL_CALL_STARTED` | A tool execution begins |
| `TOOL_CALL_FINISHED` | A tool execution completes |
| `TOOL_CALL_FAILED` | A tool execution errors |
| `BUDGET_WARNING` | Spend approaches the budget cap |
| `BUDGET_EXCEEDED` | Spend exceeds the budget cap |
| `SECURITY_VIOLATION` | A security check fails |
| `POLICY_EVALUATED` | A policy rule was checked |
| `POLICY_APPROVED` | An approver returned True |
| `POLICY_DENIED` | A policy rule blocked a tool call |
| `POLICY_LOGGED` | A log-action policy rule recorded a call |

### Subscribing

```python
from forge import Orchestrator, Event

def my_handler(event: Event) -> None:
    print(f"[{event.type.value}] agent={event.agent} data={event.data}")

async with Orchestrator() as forge:
    forge.subscribe(my_handler)
    result = await forge.run("Calculate 2 + 2")
```

Handlers must be cheap and must not raise. A misbehaving subscriber is logged
and isolated -- it cannot crash a run.

---

## Streaming

Run with `stream=True` to receive token-by-token output as it is generated:

```python
async with Orchestrator() as forge:
    result = await forge.run(
        "Write a product tagline",
        stream=True,
    )
```

Three events drive streaming:

| Event | Data |
|---|---|
| `TOKEN_STREAM_START` | Agent name |
| `TOKEN_CHUNK` | `chunk` (new text) + `cumulative` (running total) |
| `TOKEN_STREAM_END` | Agent name |

Each chunk is tagged with the agent that produced it, so you can render live
output even across parallel workers.

### CLI streaming

```bash
forge run "Write a product tagline" --stream
```

---

## OpenTelemetry

Every run becomes a tree of spans exportable to any OTel-compatible backend --
Jaeger, Grafana Tempo, Datadog, Honeycomb, New Relic.

### Enable

```python
from forge import ForgeConfig

config = ForgeConfig(
    otel_enabled=True,
    otel_service_name="forge-demo",
)
```

Or via environment:

```bash
export FORGE_OTEL_ENABLED=true
```

### Span hierarchy

```
forge.run
  forge.agent (supervisor)
    forge.model_call
    forge.agent (worker-1)
      forge.model_call
      forge.tool_call
    forge.agent (worker-2)
      forge.model_call
```

### Console export (default)

With no endpoint configured, spans are printed to the console via
`ConsoleSpanExporter`. Zero infrastructure needed.

```python
config = ForgeConfig(
    otel_enabled=True,
    otel_service_name="forge-demo",
    otel_endpoint=None,  # console output
)
```

### OTLP export (production)

Point at a real collector:

```bash
export FORGE_OTEL_ENABLED=true
export FORGE_OTEL_ENDPOINT="http://localhost:4317"
export FORGE_OTEL_SERVICE_NAME="forge-production"
```

This sends traces and metrics to any OTLP-compatible backend (Jaeger, Grafana
Tempo, Datadog Agent, etc.).

### Metrics

In addition to traces, Forge exports these metrics:

- Run count
- Run duration
- Token usage (input/output)
- Tool call count

---

## Cost reporting

Every run tracks token usage and cost per model and per agent:

```python
async with Orchestrator() as forge:
    result = await forge.run(
        "Research competitors and draft a comparison",
        mode="supervisor",
    )
    print(result.usage.format_table())
```

The usage report breaks down:

- **Per model** -- tokens in/out, cost per model
- **Per agent** -- which agent consumed what
- **Total** -- aggregate tokens and USD spend

### Budget caps

Set hard limits to prevent runaway spend:

```python
from forge import ForgeConfig, BudgetConfig

config = ForgeConfig(
    budget=BudgetConfig(
        max_usd_per_run=0.50,
        max_tokens_per_run=100_000,
    )
)
```

Two checks enforce the budget:

1. **Pre-flight** -- estimates worst-case spend of a worker batch and refuses to
   start if it would blow the budget
2. **Per-call** -- checks after every model call and halts the run if exceeded
