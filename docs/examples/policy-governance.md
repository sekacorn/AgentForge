# Policy governance

Based on `examples/policy_governance.py`. Demonstrates the three policy-as-code
governance patterns: deny, approve, and log.

## Pattern 1: Hard deny

Block a specific tool unconditionally:

```python
from forge import PolicyRule, PolicySet

policy = PolicySet()
policy.add(PolicyRule(
    name="block-calculator",
    description="Calculator is blocked by policy",
    tool_names=["calculator"],
    condition=lambda name, args: True,
    action="deny",
))
```

When the agent tries to call `calculator`, the sandbox rejects the call
immediately and returns an error result.

## Pattern 2: Human approval gate

Require a human (or automated system) to approve before a tool runs:

```python
from typing import Any

async def my_approver(tool_name: str, args: dict[str, Any]) -> bool:
    print(f"[APPROVAL GATE] Tool '{tool_name}' requested with args: {args}")
    print("[APPROVAL GATE] Auto-approving for demo purposes.")
    return True

policy = PolicySet()
policy.add(PolicyRule(
    name="require-approval-for-calculator",
    description="Calculator requires human approval",
    tool_names=["calculator"],
    condition=lambda name, args: True,
    action="approve",
    approver=my_approver,
))
```

The `approver` is an async function that receives the tool name and arguments.
Return `True` to allow, `False` to deny. In production, this could call a Slack
bot, a webhook, or a manual review queue.

## Pattern 3: Audit log

Log every tool call without blocking:

```python
policy = PolicySet()
policy.add(PolicyRule(
    name="log-all-tools",
    description="Log every tool call for audit",
    tool_names=["*"],
    condition=lambda name, args: True,
    action="log",
))
```

The wildcard `"*"` matches any tool name. Log rules continue to the next rule
(they don't short-circuit like deny/approve).

## Full example

```python
import asyncio
from forge import EchoProvider, ForgeConfig, Orchestrator, PolicyRule, PolicySet, tool

@tool
def calculator(expression: str) -> str:
    """Evaluate a math expression.

    Args:
        expression: The expression to evaluate.
    """
    return str(eval(expression))

async def main() -> None:
    policy = PolicySet()
    policy.add(PolicyRule(
        name="block-calculator",
        description="Calculator is blocked by policy",
        tool_names=["calculator"],
        condition=lambda name, args: True,
        action="deny",
    ))

    forge_instance = Orchestrator(ForgeConfig(), providers={"echo": EchoProvider()})
    async with forge_instance as forge:
        result = await forge.run(
            "Calculate 2 + 2",
            mode="single",
            tools=[calculator],
            policy_set=policy,
        )
        print(f"Output: {result.output}")

asyncio.run(main())
```

## Run it

```bash
python examples/policy_governance.py
```

## Events

Subscribe to the event bus to observe policy decisions in real time:

```python
def on_event(event):
    if "policy" in event.type.value:
        print(f"  [{event.type.value}] {event.data}")

forge.subscribe(on_event)
```

Four policy event types: `POLICY_EVALUATED`, `POLICY_APPROVED`, `POLICY_DENIED`,
`POLICY_LOGGED`.
