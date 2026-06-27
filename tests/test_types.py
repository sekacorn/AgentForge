from __future__ import annotations

from forge import Message, Role, Usage


def test_usage_is_additive() -> None:
    a = Usage(input_tokens=10, output_tokens=5, cost_usd=0.01)
    b = Usage(input_tokens=20, output_tokens=7, cost_usd=0.02)
    total = a + b
    assert total.input_tokens == 30
    assert total.output_tokens == 12
    assert total.total_tokens == 42
    assert round(total.cost_usd, 4) == 0.03


def test_usage_sum_starts_from_zero() -> None:
    usages = [Usage(input_tokens=i, output_tokens=1) for i in range(4)]
    total = sum(usages, Usage())
    assert total.input_tokens == 6
    assert total.output_tokens == 4


def test_message_constructors() -> None:
    assert Message.system("s").role is Role.SYSTEM
    assert Message.user("u").content == "u"
    msg = Message.assistant("hi")
    assert msg.role is Role.ASSISTANT and not msg.tool_calls
