"""A registry of tools available to agents."""

from __future__ import annotations

from collections.abc import Iterable

from forge.exceptions import ToolNotFoundError
from forge.tools.base import Tool
from forge.types import ToolSchema


class ToolRegistry:
    """Holds named tools and exposes their schemas to models."""

    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        """Add (or replace) a tool by name."""
        self._tools[tool.name] = tool

    def extend(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(
                f"No tool named {name!r}", context={"available": sorted(self._tools)}
            ) from exc

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[ToolSchema]:
        """Schemas for every registered tool, for advertising to a model."""
        return [tool.schema for tool in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools
