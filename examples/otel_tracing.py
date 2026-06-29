"""
OpenTelemetry tracing example — exports spans to the console.

    pip install "agentforge-oss[otel]"
    python examples/otel_tracing.py

To send traces to Jaeger, Grafana Tempo, or any OTel collector instead:

    FORGE_OTEL_ENDPOINT=http://localhost:4317 python examples/otel_tracing.py
"""

import asyncio
import os

from forge import ForgeConfig, Orchestrator


async def main() -> None:
    config = ForgeConfig(
        otel_enabled=True,
        otel_service_name="forge-demo",
        otel_endpoint=os.environ.get("FORGE_OTEL_ENDPOINT"),
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
