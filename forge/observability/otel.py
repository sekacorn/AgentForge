"""OpenTelemetry export: bridge the Forge event bus to OTel spans and metrics.

An :class:`OTelExporter` subscribes to the in-process :class:`EventBus` and turns
each run into a tree of spans (``forge.run`` -> ``forge.agent`` ->
``forge.model_call`` / ``forge.tool_call``) plus a handful of counters and
histograms, exportable to any OTel-compatible backend (Jaeger, Grafana Tempo,
Datadog, Honeycomb, New Relic).

OpenTelemetry is an *optional* dependency: every ``opentelemetry`` import is
performed lazily inside :meth:`OTelExporter.__init__`, so importing this module
(and the rest of Forge) costs nothing when OTel is disabled. Install with::

    pip install "agentforge-oss[otel]"

The exporter builds its **own** ``TracerProvider`` / ``MeterProvider`` and never
touches the global OTel providers, so it composes cleanly with a host
application that already uses OpenTelemetry.
"""

from __future__ import annotations

import time
from typing import Any

from forge.observability.events import Event, EventBus, EventType

_OTEL_MISSING = "OTel export requires opentelemetry packages: pip install 'agentforge-oss[otel]'"

# Span key tuples are ("<category>", run_id, ...). The category is never "run" for
# child spans, which lets the run-end sweep close any orphaned children first.
_SpanKey = tuple[Any, ...]


class OTelExporter:
    """Subscribes to the Forge event bus and exports spans + metrics via OTel."""

    def __init__(
        self,
        *,
        service_name: str = "forge",
        endpoint: str | None = None,
        span_exporter: Any | None = None,
        metric_reader: Any | None = None,
    ) -> None:
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        except ImportError as exc:
            raise ImportError(_OTEL_MISSING) from exc

        self._trace = trace
        resource = Resource.create({"service.name": service_name})

        if span_exporter is None:
            span_exporter = self._default_span_exporter(endpoint)
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        self._tracer_provider = tracer_provider
        self._tracer: Any = tracer_provider.get_tracer("forge")

        if metric_reader is None:
            metric_reader = self._default_metric_reader(endpoint)
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        self._meter_provider = meter_provider
        meter: Any = meter_provider.get_meter("forge")

        self._runs_total: Any = meter.create_counter("forge.runs.total")
        self._run_duration: Any = meter.create_histogram("forge.run.duration_ms", unit="ms")
        self._model_calls_total: Any = meter.create_counter("forge.model_calls.total")
        self._model_call_tokens: Any = meter.create_histogram("forge.model_call.tokens")
        self._tool_calls_total: Any = meter.create_counter("forge.tool_calls.total")
        self._budget_exceeded_total: Any = meter.create_counter("forge.budget.exceeded.total")

        self._spans: dict[_SpanKey, Any] = {}
        self._contexts: dict[_SpanKey, Any] = {}
        self._run_start: dict[str, float] = {}

    @staticmethod
    def _default_span_exporter(endpoint: str | None) -> Any:
        if endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            return OTLPSpanExporter(endpoint=endpoint, insecure=True)
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        return ConsoleSpanExporter()

    @staticmethod
    def _default_metric_reader(endpoint: str | None) -> Any:
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        if endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

            return PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=endpoint, insecure=True)
            )
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter

        return PeriodicExportingMetricReader(ConsoleMetricExporter())

    def attach(self, event_bus: EventBus) -> None:
        """Subscribe to all events on the bus. Call once after Orchestrator starts."""
        event_bus.subscribe(self._on_event)

    def shutdown(self) -> None:
        """Flush and shut down the OTel SDK. Call on Orchestrator close."""
        for span in self._spans.values():
            span.end()
        self._spans.clear()
        self._contexts.clear()
        self._run_start.clear()
        self._tracer_provider.shutdown()
        self._meter_provider.shutdown()

    # ------------------------------------------------------------------ #
    # Event handling
    # ------------------------------------------------------------------ #
    def _on_event(self, event: Event) -> None:
        handler = _HANDLERS.get(event.type)
        if handler is not None:
            handler(self, event)

    def _on_run_started(self, event: Event) -> None:
        run_id = event.run_id
        self._start_span(
            ("run", run_id),
            "forge.run",
            kind=self._trace.SpanKind.SERVER,
            parents=(),
            attrs={"forge.run_id": run_id},
        )
        if run_id is not None:
            self._run_start[run_id] = time.monotonic()

    def _on_run_finished(self, event: Event) -> None:
        run_id = event.run_id
        data = event.data
        success = bool(data.get("success"))

        # Close any child spans still open for this run (e.g. a successful worker,
        # which has no dedicated completion event) before ending the run span.
        for key in [k for k in self._spans if k[0] != "run" and len(k) > 1 and k[1] == run_id]:
            self._end_span(key)

        span = self._spans.get(("run", run_id))
        if span is not None:
            if success:
                cost = data.get("cost_usd")
                tokens = data.get("tokens")
                if cost is not None:
                    span.set_attribute("forge.cost_usd", float(cost))
                if tokens is not None:
                    span.set_attribute("forge.tokens_total", int(tokens))
            else:
                self._mark_error(span, "run_failed", str(data.get("error")))
        self._end_span(("run", run_id))

        outcome = "ok" if success else "error"
        self._runs_total.add(1, {"forge.outcome": outcome})
        start = self._run_start.pop(run_id, None) if run_id is not None else None
        if start is not None:
            self._run_duration.record(
                (time.monotonic() - start) * 1000.0, {"forge.outcome": outcome}
            )

    def _on_agent_started(self, event: Event) -> None:
        run_id, agent = event.run_id, event.agent
        self._start_span(
            ("agent", run_id, agent),
            "forge.agent",
            kind=self._trace.SpanKind.INTERNAL,
            parents=(("run", run_id),),
            attrs={"forge.run_id": run_id, "forge.agent_name": agent},
        )

    def _on_agent_finished(self, event: Event) -> None:
        run_id, agent = event.run_id, event.agent
        self._end_span(("agent", run_id, agent))
        # A worker's span (keyed by its worker_id == agent name) is closed on the
        # worker's own success, since there is no WORKER_FINISHED event.
        self._end_span(("worker", run_id, agent))

    def _on_agent_failed(self, event: Event) -> None:
        run_id, agent = event.run_id, event.agent
        reason = str(event.data.get("reason"))
        self._end_span(("agent", run_id, agent), error=("agent_failed", reason))
        self._end_span(("worker", run_id, agent), error=("agent_failed", reason))

    def _on_model_call_started(self, event: Event) -> None:
        run_id, agent, data = event.run_id, event.agent, event.data
        self._start_span(
            ("model", run_id, agent),
            "forge.model_call",
            kind=self._trace.SpanKind.CLIENT,
            parents=(("agent", run_id, agent), ("run", run_id)),
            attrs={
                "forge.run_id": run_id,
                "forge.agent_name": agent,
                "forge.model": data.get("model"),
                "forge.provider": data.get("provider"),
            },
        )

    def _on_model_call_finished(self, event: Event) -> None:
        run_id, agent, data = event.run_id, event.agent, event.data
        self._end_span(("model", run_id, agent))
        model = str(data.get("model"))
        self._model_calls_total.add(1, {"forge.model": model})
        tokens = data.get("tokens")
        if isinstance(tokens, int):
            self._model_call_tokens.record(tokens, {"forge.model": model})

    def _on_model_call_failed(self, event: Event) -> None:
        run_id, agent, data = event.run_id, event.agent, event.data
        self._end_span(
            ("model", run_id, agent), error=("model_call_failed", str(data.get("error")))
        )

    def _on_tool_call_started(self, event: Event) -> None:
        run_id, agent, data = event.run_id, event.agent, event.data
        self._start_span(
            ("tool", run_id, agent, data.get("tool")),
            "forge.tool_call",
            kind=self._trace.SpanKind.INTERNAL,
            parents=(("agent", run_id, agent), ("run", run_id)),
            attrs={
                "forge.run_id": run_id,
                "forge.agent_name": agent,
                "forge.tool_name": data.get("tool"),
            },
        )

    def _on_tool_call_finished(self, event: Event) -> None:
        run_id, agent, data = event.run_id, event.agent, event.data
        self._end_span(("tool", run_id, agent, data.get("tool")))
        self._tool_calls_total.add(1, {"forge.tool_name": str(data.get("tool"))})

    def _on_tool_call_failed(self, event: Event) -> None:
        run_id, agent, data = event.run_id, event.agent, event.data
        self._end_span(
            ("tool", run_id, agent, data.get("tool")),
            error=("tool_call_failed", str(data.get("error"))),
        )

    def _on_worker_started(self, event: Event) -> None:
        run_id, agent, data = event.run_id, event.agent, event.data
        self._start_span(
            ("worker", run_id, data.get("worker_id")),
            "forge.worker",
            kind=self._trace.SpanKind.INTERNAL,
            parents=(("agent", run_id, agent), ("run", run_id)),
            attrs={"forge.run_id": run_id, "forge.worker_id": data.get("worker_id")},
        )

    def _on_worker_failed(self, event: Event) -> None:
        run_id, data = event.run_id, event.data
        self._end_span(
            ("worker", run_id, data.get("worker_id")),
            error=("worker_failed", str(data.get("error"))),
        )

    def _on_budget_exceeded(self, event: Event) -> None:
        self._budget_exceeded_total.add(1, {"forge.run_id": str(event.run_id)})

    # ------------------------------------------------------------------ #
    # Span helpers
    # ------------------------------------------------------------------ #
    def _start_span(
        self,
        key: _SpanKey,
        name: str,
        *,
        kind: Any,
        parents: tuple[_SpanKey, ...],
        attrs: dict[str, Any],
    ) -> None:
        parent_ctx = next((self._contexts[p] for p in parents if p in self._contexts), None)
        span = self._tracer.start_span(name, context=parent_ctx, kind=kind)
        for attr_key, attr_value in attrs.items():
            if attr_value is not None:
                span.set_attribute(attr_key, attr_value)
        self._spans[key] = span
        self._contexts[key] = self._trace.set_span_in_context(span)

    def _end_span(self, key: _SpanKey, *, error: tuple[str, str] | None = None) -> None:
        span = self._spans.pop(key, None)
        self._contexts.pop(key, None)
        if span is None:
            return
        if error is not None:
            self._mark_error(span, error[0], error[1])
        span.end()

    def _mark_error(self, span: Any, error_type: str, message: str) -> None:
        span.set_attribute("error.type", error_type)
        span.set_attribute("error.message", message)
        span.set_status(self._trace.Status(self._trace.StatusCode.ERROR, message))


_HANDLERS: dict[EventType, Any] = {
    EventType.RUN_STARTED: OTelExporter._on_run_started,
    EventType.RUN_FINISHED: OTelExporter._on_run_finished,
    EventType.AGENT_STARTED: OTelExporter._on_agent_started,
    EventType.AGENT_FINISHED: OTelExporter._on_agent_finished,
    EventType.AGENT_FAILED: OTelExporter._on_agent_failed,
    EventType.MODEL_CALL_STARTED: OTelExporter._on_model_call_started,
    EventType.MODEL_CALL_FINISHED: OTelExporter._on_model_call_finished,
    EventType.MODEL_CALL_FAILED: OTelExporter._on_model_call_failed,
    EventType.TOOL_CALL_STARTED: OTelExporter._on_tool_call_started,
    EventType.TOOL_CALL_FINISHED: OTelExporter._on_tool_call_finished,
    EventType.TOOL_CALL_FAILED: OTelExporter._on_tool_call_failed,
    EventType.WORKER_STARTED: OTelExporter._on_worker_started,
    EventType.WORKER_FAILED: OTelExporter._on_worker_failed,
    EventType.BUDGET_EXCEEDED: OTelExporter._on_budget_exceeded,
}
