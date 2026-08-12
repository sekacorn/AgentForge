"""Regression tests for concurrent pre-call budget reservations."""

from __future__ import annotations

import asyncio

from forge import (
    BudgetConfig,
    BudgetExceededError,
    ForgeConfig,
    ModelInfo,
    ModelRegistry,
    ModelTier,
)
from forge.compliance.audit import AuditLogger
from forge.observability.events import EventBus
from forge.observability.usage import UsageTracker
from forge.orchestration.context import RunContext
from forge.security.sandbox import ToolSandbox
from forge.types import Message


def _context() -> RunContext:
    registry = ModelRegistry(
        [
            ModelInfo(
                name="paid",
                provider="test",
                tier=ModelTier.SMALL,
                context_window=1_000,
                max_output_tokens=100,
                input_cost_per_mtok=1_000.0,
                output_cost_per_mtok=1_000.0,
            )
        ]
    )
    return RunContext(
        run_id="run-test",
        config=ForgeConfig(budget=BudgetConfig(max_usd_per_run=0.3)),
        registry=registry,
        router=None,  # type: ignore[arg-type]
        providers={},
        sandbox=ToolSandbox(),
        usage=UsageTracker(),
        events=EventBus(),
        audit=AuditLogger("unused.jsonl", enabled=False),
    )


async def test_concurrent_reservations_do_not_oversubscribe_budget() -> None:
    ctx = _context()

    async def reserve() -> object:
        return await ctx.reserve_model_call(
            model_name="paid", messages=[Message.user("x")], tools=None, max_output_tokens=100
        )

    results = await asyncio.gather(reserve(), reserve(), return_exceptions=True)

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, BudgetExceededError) for result in results) == 1
