"""Tests for the policy-as-code governance system.

All tests are hermetic (EchoProvider, no API key).
"""

from __future__ import annotations

from typing import Any

from forge import PolicyRule, PolicySet
from forge.observability.events import EventBus, EventType
from forge.security.sandbox import ToolSandbox
from forge.tools.base import Tool
from forge.types import ToolCall


def _make_tool(name: str = "calculator", *, dangerous: bool = False) -> Tool:
    def _fn(expression: str = "1+1") -> str:
        return str(eval(expression))  # noqa: S307

    return Tool.from_function(_fn, name=name, dangerous=dangerous)


def _make_call(name: str = "calculator", args: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments=args or {"expression": "2+2"})


async def test_deny_rule_blocks_tool() -> None:
    policy = PolicySet()
    policy.add(
        PolicyRule(
            name="block-calc",
            description="Block calculator",
            tool_names=["calculator"],
            condition=lambda name, args: True,
            action="deny",
        )
    )
    sandbox = ToolSandbox(policy_set=policy)
    result = await sandbox.run(_make_tool(), _make_call())
    assert result.is_error
    assert "Denied by policy" in result.content
    assert "block-calc" in result.content


async def test_approve_rule_approver_returns_true() -> None:
    async def approver(name: str, args: dict[str, Any]) -> bool:
        return True

    policy = PolicySet()
    policy.add(
        PolicyRule(
            name="approve-calc",
            description="Approve calculator",
            tool_names=["calculator"],
            condition=lambda name, args: True,
            action="approve",
            approver=approver,
        )
    )
    sandbox = ToolSandbox(policy_set=policy)
    result = await sandbox.run(_make_tool(), _make_call())
    assert not result.is_error
    assert result.content == "4"


async def test_approve_rule_approver_returns_false() -> None:
    async def approver(name: str, args: dict[str, Any]) -> bool:
        return False

    policy = PolicySet()
    policy.add(
        PolicyRule(
            name="deny-via-approver",
            description="Human denies",
            tool_names=["calculator"],
            condition=lambda name, args: True,
            action="approve",
            approver=approver,
        )
    )
    sandbox = ToolSandbox(policy_set=policy)
    result = await sandbox.run(_make_tool(), _make_call())
    assert result.is_error
    assert "Denied by approver" in result.content


async def test_log_rule_allows_and_emits_event() -> None:
    events_captured: list[EventType] = []
    bus = EventBus()
    bus.subscribe(lambda e: events_captured.append(e.type))

    policy = PolicySet()
    policy.add(
        PolicyRule(
            name="log-all",
            description="Log all tools",
            tool_names=["*"],
            condition=lambda name, args: True,
            action="log",
        )
    )
    sandbox = ToolSandbox(events=bus, policy_set=policy)
    result = await sandbox.run(_make_tool(), _make_call())
    assert not result.is_error
    assert result.content == "4"
    assert EventType.POLICY_LOGGED in events_captured


async def test_no_policy_set_unchanged_behavior() -> None:
    sandbox = ToolSandbox()
    result = await sandbox.run(_make_tool(), _make_call())
    assert not result.is_error
    assert result.content == "4"


async def test_multiple_rules_deny_short_circuits() -> None:
    """First matching deny rule fires, later rules are skipped."""
    log_called = False

    policy = PolicySet()
    policy.add(
        PolicyRule(
            name="deny-first",
            description="Deny first",
            tool_names=["calculator"],
            condition=lambda name, args: True,
            action="deny",
        )
    )

    def log_condition(name: str, args: dict[str, Any]) -> bool:
        nonlocal log_called
        log_called = True
        return True

    policy.add(
        PolicyRule(
            name="log-second",
            description="Log second",
            tool_names=["calculator"],
            condition=log_condition,
            action="log",
        )
    )
    decision = await policy.evaluate("calculator", {"expression": "1"})
    assert not decision.allowed
    assert decision.rule_name == "deny-first"
    assert not log_called


async def test_log_rules_continue_to_next() -> None:
    """Log rules do not short-circuit -- later rules still evaluate."""
    decisions: list[str] = []

    policy = PolicySet()
    policy.add(
        PolicyRule(
            name="log-first",
            description="First log",
            tool_names=["calculator"],
            condition=lambda name, args: True,
            action="log",
        )
    )
    policy.add(
        PolicyRule(
            name="log-second",
            description="Second log",
            tool_names=["*"],
            condition=lambda name, args: decisions.append("second") or True,  # type: ignore[return-value]
            action="log",
        )
    )
    decision = await policy.evaluate("calculator", {})
    assert decision.allowed
    assert "second" in decisions


async def test_condition_false_skips_rule() -> None:
    policy = PolicySet()
    policy.add(
        PolicyRule(
            name="conditional-deny",
            description="Only deny if args contain 'secret'",
            tool_names=["calculator"],
            condition=lambda name, args: "secret" in str(args),
            action="deny",
        )
    )
    decision = await policy.evaluate("calculator", {"expression": "2+2"})
    assert decision.allowed

    decision = await policy.evaluate("calculator", {"expression": "secret"})
    assert not decision.allowed


async def test_wildcard_matches_any_tool() -> None:
    policy = PolicySet()
    policy.add(
        PolicyRule(
            name="deny-all",
            description="Deny everything",
            tool_names=["*"],
            condition=lambda name, args: True,
            action="deny",
        )
    )
    decision = await policy.evaluate("any_tool_name", {})
    assert not decision.allowed


async def test_run_with_policy_set_none_no_regression() -> None:
    """Orchestrator.run() with policy_set=None is identical to the old path."""
    from forge import EchoProvider, ForgeConfig, Orchestrator

    config = ForgeConfig()
    async with Orchestrator(config, providers={"echo": EchoProvider()}) as forge:
        result = await forge.run("Hello", mode="single", policy_set=None)
        assert result.success


async def test_deny_event_emitted() -> None:
    events_captured: list[EventType] = []
    bus = EventBus()
    bus.subscribe(lambda e: events_captured.append(e.type))

    policy = PolicySet()
    policy.add(
        PolicyRule(
            name="deny-calc",
            description="Block calculator",
            tool_names=["calculator"],
            condition=lambda name, args: True,
            action="deny",
        )
    )
    sandbox = ToolSandbox(events=bus, policy_set=policy)
    await sandbox.run(_make_tool(), _make_call())
    assert EventType.POLICY_DENIED in events_captured
