"""An exception's own text never leaves the local log.

Prompts sent to a model, audit records, events and the OpenTelemetry spans
built from them carry the exception type and a fixed description only. The
full text, with its traceback, goes to the local ``forge.failures`` log.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from forge.config import ForgeConfig
from forge.models.providers.echo import PLAN_MARKER, SYNTH_MARKER, EchoProvider
from forge.observability.events import Event, EventType
from forge.observability.failures import (
    MODEL_CALL_FAILED,
    RUN_FAILED,
    WORKER_FAILED,
    public_failure,
)
from forge.orchestration.orchestrator import Orchestrator
from forge.types import Message, ModelResponse

SECRET = "failure-text-secret-9b40-do-not-repeat"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            text += f" | {record.exc_info[1]}"
        self.lines.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def local_log() -> Any:
    handler = _Capture()
    logger = logging.getLogger("forge.failures")
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)


class _FailWorker(EchoProvider):
    """Fails any worker call whose subtask contains the marker, recording synthesis."""

    def __init__(self) -> None:
        self.synthesis_prompts: list[str] = []

    async def complete(self, messages: list[Message], **kwargs: Any) -> ModelResponse:
        text = " ".join(m.content for m in messages)
        if SYNTH_MARKER in text:
            self.synthesis_prompts.append(text)
        elif PLAN_MARKER not in text and "FAILME" in text:
            raise RuntimeError(f"upstream said: {SECRET}")
        return await super().complete(messages, **kwargs)


class _FailEverything(EchoProvider):
    async def complete(self, messages: list[Message], **kwargs: Any) -> ModelResponse:
        raise RuntimeError(f"upstream said: {SECRET}")


def _orchestrator(tmp_path: Path, provider: Any) -> tuple[Orchestrator, list[Event]]:
    config = ForgeConfig()
    config.compliance.audit_path = str(tmp_path / "audit.jsonl")
    orchestrator = Orchestrator(config, providers={"echo": provider})
    events: list[Event] = []
    orchestrator.subscribe(events.append)
    return orchestrator, events


def _event_text(events: list[Event]) -> str:
    return json.dumps([{"type": e.type.value, "data": e.data} for e in events], default=str)


def test_the_public_text_is_the_type_and_a_fixed_description() -> None:
    assert public_failure(ValueError(SECRET), WORKER_FAILED) == "ValueError: worker failed"


async def test_a_failed_worker_never_puts_its_error_text_in_the_synthesis_prompt(
    tmp_path: Path, local_log: _Capture
) -> None:
    provider = _FailWorker()
    orchestrator, events = _orchestrator(tmp_path, provider)

    result = await orchestrator.run("alpha and FAILME and gamma", tools=[])

    assert provider.synthesis_prompts, "the supervisor should have synthesized"
    prompt = "\n".join(provider.synthesis_prompts)
    assert SECRET not in prompt
    assert f"RuntimeError: {WORKER_FAILED}" in prompt
    assert SECRET not in result.output
    # The full text is kept, locally.
    assert SECRET in local_log.text


async def test_a_failed_worker_is_audited_and_announced_without_its_text(
    tmp_path: Path, local_log: _Capture
) -> None:
    orchestrator, events = _orchestrator(tmp_path, _FailWorker())

    await orchestrator.run("alpha and FAILME and gamma", tools=[])

    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert SECRET not in audit
    record = next(json.loads(line) for line in audit.splitlines() if '"worker.failed"' in line)
    assert json.dumps(record).count(f"RuntimeError: {WORKER_FAILED}") >= 1
    failed = [e for e in events if e.type is EventType.WORKER_FAILED]
    assert failed and failed[0].data["error"] == f"RuntimeError: {WORKER_FAILED}"
    assert failed[0].data["error_type"] == "RuntimeError"
    assert SECRET not in _event_text(events)
    assert orchestrator.verify_audit() is True


async def test_a_failed_model_call_and_run_are_announced_and_audited_without_text(
    tmp_path: Path, local_log: _Capture
) -> None:
    orchestrator, events = _orchestrator(tmp_path, _FailEverything())

    with pytest.raises(RuntimeError):
        await orchestrator.run("anything", mode="single")

    by_type = {e.type: e for e in events}
    assert (
        by_type[EventType.MODEL_CALL_FAILED].data["error"] == f"RuntimeError: {MODEL_CALL_FAILED}"
    )
    assert by_type[EventType.RUN_FINISHED].data["error"] == f"RuntimeError: {RUN_FAILED}"
    assert SECRET not in _event_text(events)
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert SECRET not in audit
    assert f"RuntimeError: {RUN_FAILED}" in audit
    assert SECRET in local_log.text
    assert orchestrator.verify_audit() is True


async def test_telemetry_spans_never_carry_the_error_text(tmp_path: Path) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from forge.observability.otel import OTelExporter

    spans = InMemorySpanExporter()
    exporter = OTelExporter(span_exporter=spans, metric_reader=InMemoryMetricReader())
    orchestrator, _ = _orchestrator(tmp_path, _FailWorker())
    exporter.attach(orchestrator.events)

    await orchestrator.run("alpha and FAILME and gamma", tools=[])

    finished = spans.get_finished_spans()
    assert finished
    dumped = json.dumps(
        [
            {
                "attrs": dict(s.attributes or {}),
                "status": s.status.description,
                "events": [dict(e.attributes or {}) for e in s.events],
            }
            for s in finished
        ],
        default=str,
    )
    assert SECRET not in dumped
