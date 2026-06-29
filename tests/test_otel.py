"""Tests for OpenTelemetry export.

Hermetic: drives the exporter with OTel's in-memory ``InMemorySpanExporter`` and
``InMemoryMetricReader`` so no collector (and no network) is required. The whole
module skips cleanly when the optional ``opentelemetry`` packages are absent.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from forge import (  # noqa: E402
    EchoProvider,
    ForgeConfig,
    Orchestrator,
    ToolRegistry,
    calculator,
)
from forge.observability.otel import OTelExporter  # noqa: E402


def _orchestrator(tmp_path) -> Orchestrator:
    cfg = ForgeConfig()
    cfg.compliance.audit_path = str(tmp_path / "audit.jsonl")
    return Orchestrator(cfg, providers={"echo": EchoProvider()})


async def _run_capturing(orchestrator, goal, **run_kwargs):
    span_exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    exporter = OTelExporter(span_exporter=span_exporter, metric_reader=reader)
    exporter.attach(orchestrator.events)
    try:
        await orchestrator.run(goal, **run_kwargs)
        spans = span_exporter.get_finished_spans()
        metrics = reader.get_metrics_data()
    finally:
        exporter.shutdown()
        await orchestrator.aclose()
    return spans, metrics


def _span(spans, name):
    return next((s for s in spans if s.name == name), None)


def _counter_total(metrics_data, name: str) -> float:
    total = 0.0
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == name:
                    for point in metric.data.data_points:
                        total += point.value
    return total


async def test_run_root_span_created(tmp_path) -> None:
    spans, _ = await _run_capturing(_orchestrator(tmp_path), "hello", mode="single", tools=[])
    run_span = _span(spans, "forge.run")
    assert run_span is not None
    assert run_span.attributes["forge.run_id"]


async def test_agent_span_has_agent_name(tmp_path) -> None:
    spans, _ = await _run_capturing(_orchestrator(tmp_path), "hello", mode="single", tools=[])
    agent_span = _span(spans, "forge.agent")
    assert agent_span is not None
    assert agent_span.attributes["forge.agent_name"] == "agent"


async def test_model_call_span_has_model_and_provider(tmp_path) -> None:
    spans, _ = await _run_capturing(_orchestrator(tmp_path), "hello", mode="single", tools=[])
    model_span = _span(spans, "forge.model_call")
    assert model_span is not None
    assert model_span.attributes["forge.model"]
    assert model_span.attributes["forge.provider"] == "echo"


async def test_tool_call_span_has_tool_name(tmp_path) -> None:
    tools = ToolRegistry([calculator])
    spans, _ = await _run_capturing(
        _orchestrator(tmp_path), "compute 6 * 7", mode="single", tools=tools
    )
    tool_span = _span(spans, "forge.tool_call")
    assert tool_span is not None
    assert tool_span.attributes["forge.tool_name"] == "calculator"


async def test_runs_counter_incremented(tmp_path) -> None:
    _, metrics = await _run_capturing(_orchestrator(tmp_path), "hello", mode="single", tools=[])
    assert _counter_total(metrics, "forge.runs.total") == 1.0


async def test_disabled_by_default_no_exporter(tmp_path) -> None:
    cfg = ForgeConfig()
    cfg.compliance.audit_path = str(tmp_path / "audit.jsonl")
    assert cfg.otel_enabled is False
    async with Orchestrator(cfg, providers={"echo": EchoProvider()}) as orch:
        # No OTel machinery is created when the feature is off.
        assert orch._otel_exporter is None
        result = await orch.run("hello", mode="single", tools=[])
    assert result.output


async def test_helpful_import_error_when_packages_missing(monkeypatch) -> None:
    # Simulate opentelemetry being absent even though it is installed here.
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    with pytest.raises(ImportError, match=r"agentforge-oss\[otel\]"):
        OTelExporter()
