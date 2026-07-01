# OpenTelemetry tracing

Based on `examples/otel_tracing.py`. Shows console span export and how to wire
up a real OTel collector.

## Console export (zero infra)

```python
import asyncio
from forge import ForgeConfig, Orchestrator

async def main() -> None:
    config = ForgeConfig(
        otel_enabled=True,
        otel_service_name="forge-demo",
    )
    async with Orchestrator(config) as forge:
        result = await forge.run(
            "Calculate 15% of 4200 and summarize the result",
            mode="single",
        )
        print(result.output)
        print(result.usage.format_table())
        print("\nSpans exported above (ConsoleSpanExporter).")

asyncio.run(main())
```

## Install

```bash
pip install "agentforge-oss[otel]"
```

## Run it

```bash
python examples/otel_tracing.py
```

Spans are printed to the console by default. You'll see a tree of spans:

```
forge.run
  forge.agent
    forge.model_call
    forge.tool_call
```

## OTLP export (production)

Point at a real collector to send traces to Jaeger, Grafana Tempo, Datadog, or
any OTLP-compatible backend:

```bash
export FORGE_OTEL_ENABLED=true
export FORGE_OTEL_ENDPOINT="http://localhost:4317"
export FORGE_OTEL_SERVICE_NAME="forge-production"
python examples/otel_tracing.py
```

Or configure programmatically:

```python
import os

config = ForgeConfig(
    otel_enabled=True,
    otel_service_name="forge-production",
    otel_endpoint=os.environ.get("FORGE_OTEL_ENDPOINT"),
)
```

## Jaeger quick setup

To see traces visually with Jaeger:

```bash
docker run -d --name jaeger \
  -p 16686:16686 \
  -p 4317:4317 \
  jaegertracing/all-in-one:latest

export FORGE_OTEL_ENABLED=true
export FORGE_OTEL_ENDPOINT="http://localhost:4317"
python examples/otel_tracing.py
```

Open `http://localhost:16686` to browse traces.

## Metrics

In addition to traces, Forge exports these metrics when OTel is enabled:

- Run count
- Run duration
- Token usage (input/output)
- Tool call count
