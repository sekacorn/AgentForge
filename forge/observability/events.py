"""A lightweight, in-process event bus.

Every meaningful lifecycle moment (an agent starting, a model call finishing, a
tool failing, a budget threshold being crossed) is emitted as an :class:`Event`.
Subscribers — a console renderer, the audit log, a metrics exporter — observe
the same stream without the producers knowing who is listening.

The bus is intentionally synchronous and dependency-free. Handlers must be cheap
and must not raise: a misbehaving subscriber is logged and isolated so it cannot
break an agent run.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from forge.observability.logging import get_logger
from forge.types import new_id, utcnow

_log = get_logger("events")


class EventType(enum.StrEnum):
    """The catalogue of lifecycle events Forge emits."""

    RUN_STARTED = "run.started"
    RUN_FINISHED = "run.finished"
    PLAN_CREATED = "plan.created"
    AGENT_STARTED = "agent.started"
    AGENT_FINISHED = "agent.finished"
    AGENT_FAILED = "agent.failed"
    WORKER_STARTED = "worker.started"
    WORKER_FAILED = "worker.failed"
    MODEL_CALL_STARTED = "model.call.started"
    MODEL_CALL_FINISHED = "model.call.finished"
    MODEL_CALL_FAILED = "model.call.failed"
    MODEL_ROUTED = "model.routed"
    TOOL_CALL_STARTED = "tool.call.started"
    TOOL_CALL_FINISHED = "tool.call.finished"
    TOOL_CALL_FAILED = "tool.call.failed"
    BUDGET_WARNING = "budget.warning"
    BUDGET_EXCEEDED = "budget.exceeded"
    SECURITY_VIOLATION = "security.violation"


EventHandler = Callable[["Event"], None]


class Event(BaseModel):
    """An immutable record of something that happened during a run."""

    id: str = Field(default_factory=lambda: new_id("evt"))
    type: EventType
    timestamp: Any = Field(default_factory=utcnow)
    run_id: str | None = None
    agent: str | None = None
    #: Arbitrary structured payload. Keep it free of secrets.
    data: dict[str, Any] = Field(default_factory=dict)


class EventBus:
    """Fan-out hub: producers ``emit`` events, subscribers receive them."""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        """Register a callback invoked for every subsequent event."""
        self._handlers.append(handler)

    def emit(
        self,
        type: EventType,
        *,
        run_id: str | None = None,
        agent: str | None = None,
        **data: Any,
    ) -> Event:
        """Construct and dispatch an event to all subscribers.

        Handler exceptions are caught and logged so one bad subscriber can never
        crash a run.
        """
        event = Event(type=type, run_id=run_id, agent=agent, data=data)
        for handler in self._handlers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - isolation is the whole point
                _log.exception("event handler failed for %s", event.type.value)
        return event
