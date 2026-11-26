from __future__ import annotations

import pytest

from forge import DuplicateToolError, ToolNotFoundError, ToolRegistry, calculator, tool
from forge.exceptions import ToolError
from forge.tools.base import Tool


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


def _named(name: str, answer: str) -> Tool:
    def body() -> str:
        """Return a fixed answer."""
        return answer

    return tool(name=name)(body)


def test_registry_refuses_a_same_named_tool() -> None:
    original = _named("lookup", "original")
    impostor = _named("lookup", "impostor")
    registry = ToolRegistry([original])

    with pytest.raises(DuplicateToolError, match="already registered") as caught:
        registry.register(impostor)

    assert caught.value.context == {"tool": "lookup"}
    assert registry.get("lookup") is original


def test_registry_refuses_duplicates_in_the_constructor_and_extend() -> None:
    with pytest.raises(DuplicateToolError):
        ToolRegistry([_named("lookup", "a"), _named("lookup", "b")])
    registry = ToolRegistry([calculator])
    with pytest.raises(DuplicateToolError):
        registry.extend([_named("fresh", "x"), _named("calculator", "impostor")])
    # Tools before the duplicate stay registered; the original is untouched.
    assert registry.has("fresh") and registry.get("calculator") is calculator


def test_registry_replaces_only_when_asked() -> None:
    original = _named("lookup", "original")
    replacement = _named("lookup", "replacement")
    registry = ToolRegistry([original])

    registry.register(replacement, replace=True)
    assert registry.get("lookup") is replacement

    registry.extend([original], replace=True)
    assert registry.get("lookup") is original


def test_registering_the_same_tool_object_again_is_a_no_op() -> None:
    registry = ToolRegistry([calculator])
    registry.register(calculator)
    registry.extend([calculator])
    assert registry.names() == ["calculator"] and registry.get("calculator") is calculator


def test_duplicate_tool_error_is_a_tool_error() -> None:
    assert issubclass(DuplicateToolError, ToolError)
