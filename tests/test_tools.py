from __future__ import annotations

import pytest

from forge import ToolNotFoundError, ToolRegistry, calculator, tool


def test_tool_schema_is_extracted_from_hints_and_docstring() -> None:
    @tool
    def add(a: int, b: int = 0) -> int:
        """Add two integers.

        Args:
            a: First addend.
            b: Second addend.
        """
        return a + b

    schema = add.schema
    assert schema.name == "add"
    assert schema.description == "Add two integers."
    props = schema.parameters["properties"]
    assert props["a"]["type"] == "integer"
    assert props["a"]["description"] == "First addend."
    assert props["b"]["default"] == 0
    # Only ``a`` is required (``b`` has a default).
    assert schema.parameters["required"] == ["a"]


async def test_calculator_evaluates_safely() -> None:
    assert await calculator.invoke({"expression": "2 + 3 * (4 - 1)"}) == "11"
    assert await calculator.invoke({"expression": "10 / 4"}) == "2.5"
    # Division by zero is reported, not raised.
    assert "Error" in await calculator.invoke({"expression": "1 / 0"})
    # Arbitrary code is rejected (no eval).
    assert "Error" in await calculator.invoke({"expression": "__import__('os')"})


def test_registry_lookup() -> None:
    registry = ToolRegistry([calculator])
    assert registry.has("calculator")
    assert "calculator" in registry
    with pytest.raises(ToolNotFoundError):
        registry.get("nope")
