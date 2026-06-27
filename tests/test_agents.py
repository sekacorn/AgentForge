from __future__ import annotations

import pytest

from forge import (
    BudgetConfig,
    ForgeConfig,
    MaxStepsExceededError,
    ToolRegistry,
    calculator,
)


async def test_single_agent_completes_tool_loop(make_orchestrator) -> None:
    orchestrator = make_orchestrator()
    result = await orchestrator.run(
        "what is 6 * 7", mode="single", tools=ToolRegistry([calculator])
    )
    # The agent should call the calculator and report 42 in its final answer.
    assert "42" in result.output
    assert result.usage.num_calls >= 2  # tool-call turn + synthesis turn


async def test_agent_respects_step_budget(make_orchestrator) -> None:
    # With a one-step budget, a task that needs a tool call cannot finish.
    config = ForgeConfig(budget=BudgetConfig(max_steps_per_agent=1))
    orchestrator = make_orchestrator(config)
    with pytest.raises(MaxStepsExceededError):
        await orchestrator.run("compute 2 + 2", mode="single", tools=ToolRegistry([calculator]))
