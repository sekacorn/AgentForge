"""Observability: structured logging, an event bus, and usage/cost tracking."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from forge.observability.events import Event, EventBus, EventType
from forge.observability.logging import configure_logging, get_logger
from forge.observability.usage import UsageRecord, UsageReport, UsageTracker

if TYPE_CHECKING:
    from forge.observability.otel import OTelExporter

__all__ = [
    "Event",
    "EventBus",
    "EventType",
    "configure_logging",
    "get_logger",
    "UsageRecord",
    "UsageReport",
    "UsageTracker",
    "OTelExporter",
]


def __getattr__(name: str) -> Any:
    # Lazy export so importing this package never pulls in opentelemetry (a heavy,
    # optional dependency). ``OTelExporter`` is materialized only on first access.
    if name == "OTelExporter":
        from forge.observability.otel import OTelExporter

        return OTelExporter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
