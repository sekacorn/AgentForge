"""Tests for concurrent worker execution in the Supervisor.

All hermetic: offline EchoProvider subclasses, no API key, no real delays. The
``ConcurrencyProbeProvider`` records the peak number of in-flight ``complete()``
calls; an ``await asyncio.sleep(0)`` makes concurrent workers interleave on the
event loop without any wall-clock delay, so concurrency is proven deterministically.
"""

from __future__ import annotations

import asyncio

import pytest

from forge import (
    BudgetConfig,
    BudgetExceededError,
    EchoProvider,
    EventType,
    ForgeConfig,
    ModelInfo,
    ModelRegistry,
    ModelTier,
    Orchestrator,
)
from forge.models.providers.echo import PLAN_MARKER, SYNTH_MARKER


class ConcurrencyProbeProvider(EchoProvider):
    """EchoProvider that records the peak concurrency of in-flight completions."""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.complete_calls = 0

    async def complete(self, messages, *, model, tools=None, system=None, max_tokens=4096, **opts):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.complete_calls += 1
        try:
            await asyncio.sleep(0)  # yield so concurrent workers interleave
            return await super().complete(
                messages, model=model, tools=tools, system=system, max_tokens=max_tokens, **opts
            )
        finally:
            self.active -= 1


class FailOnSubtaskProvider(EchoProvider):
    """EchoProvider that raises for any worker whose subtask contains ``marker``.

    Planning and synthesis calls (identified by their markers) are left to succeed
    so only the targeted worker fails.
    """

    def __init__(self, marker: str) -> None:
        self.marker = marker

    async def complete(self, messages, *, model, tools=None, system=None, max_tokens=4096, **opts):
        text = " ".join(m.content for m in messages)
        is_planning = PLAN_MARKER in text
        is_synthesis = SYNTH_MARKER in text
        if not is_planning and not is_synthesis and self.marker in text:
            raise RuntimeError(f"simulated failure for subtask: {self.marker}")
        return await super().complete(
            messages, model=model, tools=tools, system=system, max_tokens=max_tokens, **opts
        )


def _build(tmp_path, provider, *, config=None, registry=None):
    cfg = config or ForgeConfig()
    cfg.compliance.audit_path = str(tmp_path / "audit.jsonl")
    return Orchestrator(cfg, providers={"echo": provider}, registry=registry)


def _paid_registry() -> ModelRegistry:
    """An echo registry priced high enough that the pre-flight check can fire."""
    models = [
        ModelInfo(
            name="echo-mini",
            provider="echo",
            tier=ModelTier.SMALL,
            context_window=128_000,
            max_output_tokens=8_192,
            input_cost_per_mtok=1000.0,
            output_cost_per_mtok=1000.0,
            supports_tools=True,
        ),
        ModelInfo(
            name="echo-pro",
            provider="echo",
            tier=ModelTier.LARGE,
            context_window=128_000,
            max_output_tokens=8_192,
            input_cost_per_mtok=1000.0,
            output_cost_per_mtok=1000.0,
            supports_tools=True,
        ),
    ]
    return ModelRegistry(models)


async def test_workers_run_concurrently(tmp_path):
    probe = ConcurrencyProbeProvider()
    orchestrator = _build(tmp_path, probe)

    started: list[str] = []

    def on_event(event):
        if event.type is EventType.WORKER_STARTED:
            started.append(event.data["worker_id"])

    orchestrator.subscribe(on_event)

    await orchestrator.run("task one and task two and task three", tools=[])

    # All three workers were in flight at the same moment (1x, not 3x, the work).
    assert probe.max_active == 3
    assert len(started) == 3


async def test_failing_worker_does_not_crash_run(tmp_path):
    orchestrator = _build(tmp_path, FailOnSubtaskProvider(marker="FAILME"))

    seen: list[EventType] = []
    orchestrator.subscribe(lambda event: seen.append(event.type))

    result = await orchestrator.run("alpha and FAILME and gamma", tools=[])

    # The run completed and the healthy subtasks still produced output.
    assert "alpha" in result.output
    assert "gamma" in result.output
    # The failed subtask's slot carries a graceful error note rather than crashing.
    assert "[failed]" in result.output
    assert "FAILME" in result.output
    # A worker-failure event was emitted and the audit chain stays intact.
    assert EventType.WORKER_FAILED in seen
    assert orchestrator.verify_audit() is True


async def test_max_workers_bounds_parallelism(tmp_path):
    probe = ConcurrencyProbeProvider()
    config = ForgeConfig(budget=BudgetConfig(max_workers=2))
    orchestrator = _build(tmp_path, probe, config=config)

    await orchestrator.run("one and two and three and four", tools=[])

    # 4 subtasks with max_workers=2 run as 2 batches of 2 -> never more than 2 at once.
    assert probe.max_active == 2


async def test_preflight_budget_blocks_before_workers(tmp_path):
    probe = ConcurrencyProbeProvider()
    config = ForgeConfig(budget=BudgetConfig(max_usd_per_run=1.0))
    orchestrator = _build(tmp_path, probe, config=config, registry=_paid_registry())

    seen: list[EventType] = []
    orchestrator.subscribe(lambda event: seen.append(event.type))

    with pytest.raises(BudgetExceededError):
        await orchestrator.run("alpha and beta", tools=[])

    # The pre-flight guard fired before any worker coroutine started.
    assert EventType.WORKER_STARTED not in seen
    # Only the sequential planning call ran — no worker reached the provider.
    assert probe.max_active == 1
