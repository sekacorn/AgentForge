"""Policy-as-code governance for tool execution.

Operators define :class:`PolicyRule` instances that evaluate *before* a tool call
runs. Rules can hard-deny, require human approval (an async callback), or simply
log the event. A :class:`PolicySet` evaluates rules in order and produces a
:class:`PolicyDecision`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class PolicyRule:
    """A single governance rule evaluated before a tool call executes."""

    name: str
    description: str
    tool_names: list[str]
    condition: Callable[[str, dict[str, Any]], bool]
    action: Literal["approve", "deny", "log"]
    approver: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None


@dataclass
class PolicyDecision:
    """The outcome of evaluating a :class:`PolicySet` against a tool call."""

    allowed: bool
    rule_name: str | None
    action_taken: str
    reason: str


@dataclass
class PolicySet:
    """An ordered collection of :class:`PolicyRule` instances evaluated in sequence."""

    rules: list[PolicyRule] = field(default_factory=list)

    def add(self, rule: PolicyRule) -> None:
        """Append a rule to the evaluation chain."""
        self.rules.append(rule)

    async def evaluate(self, tool_name: str, args: dict[str, Any]) -> PolicyDecision:
        """Evaluate all rules against a pending tool call.

        Rules are checked in order. ``deny`` and ``approve`` rules short-circuit
        (only the first matching rule of these types fires). ``log`` rules always
        continue to the next rule. If no rule matches, the call is allowed.
        """
        log_decisions: list[PolicyDecision] = []

        for rule in self.rules:
            if not self._matches(rule, tool_name):
                continue
            if not rule.condition(tool_name, args):
                continue

            if rule.action == "deny":
                return PolicyDecision(
                    allowed=False,
                    rule_name=rule.name,
                    action_taken="denied",
                    reason=f"Denied by policy rule '{rule.name}': {rule.description}",
                )

            if rule.action == "approve":
                if rule.approver is None:
                    return PolicyDecision(
                        allowed=False,
                        rule_name=rule.name,
                        action_taken="denied",
                        reason=(
                            f"Policy rule '{rule.name}' requires an approver but none is configured"
                        ),
                    )
                approved = await rule.approver(tool_name, args)
                if approved:
                    return PolicyDecision(
                        allowed=True,
                        rule_name=rule.name,
                        action_taken="approved",
                        reason=f"Approved by policy rule '{rule.name}'",
                    )
                return PolicyDecision(
                    allowed=False,
                    rule_name=rule.name,
                    action_taken="denied",
                    reason=f"Denied by approver for policy rule '{rule.name}'",
                )

            if rule.action == "log":
                log_decisions.append(
                    PolicyDecision(
                        allowed=True,
                        rule_name=rule.name,
                        action_taken="logged",
                        reason=f"Logged by policy rule '{rule.name}'",
                    )
                )

        if log_decisions:
            return log_decisions[-1]

        return PolicyDecision(
            allowed=True,
            rule_name=None,
            action_taken="allowed",
            reason="No policy rule matched",
        )

    @staticmethod
    def _matches(rule: PolicyRule, tool_name: str) -> bool:
        return "*" in rule.tool_names or tool_name in rule.tool_names
