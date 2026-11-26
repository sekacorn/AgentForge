"""A registry of tools available to agents."""

from __future__ import annotations

from collections.abc import Iterable

from forge.exceptions import DuplicateToolError, ToolNotFoundError
from forge.tools.base import Tool
from forge.types import ToolSchema


class ToolRegistry:
    """Holds named tools and exposes their schemas to models."""

    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool, *, replace: bool = False) -> None:
        """Add a tool by name.

        A name that is already registered is refused with
        :class:`~forge.exceptions.DuplicateToolError` unless ``replace=True``.
        Silently swapping a tool for another of the same name would change what
        an agent runs without anyone seeing it, so replacement must be asked for.
        Registering the very same tool object again is a no-op.
        """
        existing = self._tools.get(tool.name)
        if existing is not None and existing is not tool and not replace:
            raise DuplicateToolError(
                f"A tool named {tool.name!r} is already registered; "
                "pass replace=True to replace it.",
                context={"tool": tool.name},
            )
        self._tools[tool.name] = tool

    def extend(self, tools: Iterable[Tool], *, replace: bool = False) -> None:
        """Register several tools; see :meth:`register` for duplicates."""
        for tool in tools:
            self.register(tool, replace=replace)

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
