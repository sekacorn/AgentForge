"""Policy-as-code governance example.

Demonstrates three governance patterns:
1. Hard deny -- block a specific tool unconditionally
2. Human approval gate -- require a human to approve before a dangerous tool runs
3. Audit log -- log every tool call without blocking it

    python examples/policy_governance.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from forge import EchoProvider, ForgeConfig, Orchestrator, PolicyRule, PolicySet, tool


@tool
def calculator(expression: str) -> str:
    """Evaluate a math expression.

    Args:
        expression: The expression to evaluate.
    """
    return str(eval(expression))  # noqa: S307


def _echo_orchestrator() -> Orchestrator:
    return Orchestrator(ForgeConfig(), providers={"echo": EchoProvider()})


async def demo_deny() -> None:
    """Pattern 1: hard deny calculator unconditionally."""
    print("--- Pattern 1: Hard Deny ---")

    policy = PolicySet()
    policy.add(
        PolicyRule(
            name="block-calculator",
            description="Calculator is blocked by policy",
            tool_names=["calculator"],
            condition=lambda name, args: True,
            action="deny",
        )
    )

    async with _echo_orchestrator() as forge:
        result = await forge.run(
            "Calculate 2 + 2",
            mode="single",
            tools=[calculator],
            policy_set=policy,
        )
        print(f"  Output: {result.output}")
    print()


async def demo_approval() -> None:
    """Pattern 2: human approval gate -- approver always approves in this demo."""
    print("--- Pattern 2: Human Approval Gate ---")

    async def my_approver(tool_name: str, args: dict[str, Any]) -> bool:
        print(f"  [APPROVAL GATE] Tool '{tool_name}' requested with args: {args}")
        print("  [APPROVAL GATE] Auto-approving for demo purposes.")
        return True

    policy = PolicySet()
    policy.add(
        PolicyRule(
            name="require-approval-for-calculator",
            description="Calculator requires human approval",
            tool_names=["calculator"],
            condition=lambda name, args: True,
            action="approve",
            approver=my_approver,
        )
    )

    async with _echo_orchestrator() as forge:
        result = await forge.run(
            "Calculate 10 * 5",
            mode="single",
            tools=[calculator],
            policy_set=policy,
        )
        print(f"  Output: {result.output}")
    print()


async def demo_audit_log() -> None:
    """Pattern 3: log all tool calls without blocking."""
    print("--- Pattern 3: Audit Log ---")

    logged_events: list[str] = []

    policy = PolicySet()
    policy.add(
        PolicyRule(
            name="log-all-tools",
            description="Log every tool call for audit",
            tool_names=["*"],
            condition=lambda name, args: True,
            action="log",
        )
    )

    async with _echo_orchestrator() as forge:

        def on_event(event: object) -> None:
            logged_events.append(str(getattr(event, "type", "")))

        forge.subscribe(on_event)
        result = await forge.run(
            "Calculate 3 + 7",
            mode="single",
            tools=[calculator],
            policy_set=policy,
        )
        print(f"  Output: {result.output}")
        policy_events = [e for e in logged_events if "policy" in e]
        print(f"  Policy events emitted: {policy_events}")
    print()


if __name__ == "__main__":
    asyncio.run(demo_deny())
    asyncio.run(demo_approval())
    asyncio.run(demo_audit_log())
