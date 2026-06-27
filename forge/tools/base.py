"""The tool abstraction and the ``@tool`` decorator.

A :class:`Tool` wraps a Python callable and exposes it to agents with a
JSON-Schema description derived automatically from the function's type hints and
docstring. Both sync and async callables are supported.

Example
-------
>>> @tool
... def add(a: int, b: int) -> int:
...     '''Add two integers.
...
...     Args:
...         a: First addend.
...         b: Second addend.
...     '''
...     return a + b
>>> add.schema.name
'add'

Tools carry a ``dangerous`` flag (network/filesystem/side-effecting) so the
security and governance layers can gate them — see
:mod:`forge.security.sandbox`.
"""

from __future__ import annotations

import inspect
import json
import re
import typing
from collections.abc import Callable
from typing import Any, get_type_hints, overload

from forge.exceptions import ToolValidationError
from forge.types import ToolSchema

_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _json_type(annotation: Any) -> dict[str, Any]:
    """Translate a Python annotation into a (best-effort) JSON Schema fragment."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}

    origin = typing.get_origin(annotation)
    if origin is not None:
        # Unwrap Optional[X] / Union[X, None] to X.
        if origin is typing.Union:
            args = [a for a in typing.get_args(annotation) if a is not type(None)]
            if len(args) == 1:
                return _json_type(args[0])
            return {"type": "string"}
        if origin in (list, set, tuple):
            return {"type": "array"}
        if origin is dict:
            return {"type": "object"}

    if isinstance(annotation, type):
        return {"type": _PY_TO_JSON.get(annotation, "string")}
    return {"type": "string"}


def _parse_arg_docs(docstring: str | None) -> dict[str, str]:
    """Extract per-argument descriptions from a Google-style ``Args:`` block."""
    if not docstring:
        return {}
    descriptions: dict[str, str] = {}
    in_args = False
    for line in docstring.splitlines():
        stripped = line.strip()
        if stripped.lower() in {"args:", "arguments:", "parameters:"}:
            in_args = True
            continue
        if in_args:
            if not stripped or stripped.endswith(":") and " " not in stripped:
                break
            match = re.match(r"([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*:\s*(.+)", stripped)
            if match:
                descriptions[match.group(1)] = match.group(2).strip()
    return descriptions


def _summary(docstring: str | None) -> str:
    """First paragraph of a docstring, used as the tool description."""
    if not docstring:
        return ""
    lines: list[str] = []
    for raw in docstring.strip().splitlines():
        line = raw.strip()
        if not line:
            break
        if line.lower() in {"args:", "arguments:", "parameters:"}:
            break
        lines.append(line)
    return " ".join(lines)


class Tool:
    """A callable exposed to agents, with an auto-generated schema."""

    def __init__(
        self,
        func: Callable[..., Any],
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        dangerous: bool = False,
    ) -> None:
        self._func = func
        self.name = name
        self.description = description
        self.parameters = parameters
        self.is_async = inspect.iscoroutinefunction(func)
        #: True for tools with side effects (network, filesystem, mutations).
        self.dangerous = dangerous

    @property
    def schema(self) -> ToolSchema:
        """The provider-agnostic schema advertised to models."""
        return ToolSchema(name=self.name, description=self.description, parameters=self.parameters)

    @classmethod
    def from_function(
        cls,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        dangerous: bool = False,
    ) -> Tool:
        """Build a :class:`Tool` by introspecting ``func``."""
        hints = get_type_hints(func)
        arg_docs = _parse_arg_docs(func.__doc__)
        signature = inspect.signature(func)

        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in signature.parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            if param_name == "self":
                continue
            schema = _json_type(hints.get(param_name, param.annotation))
            if param_name in arg_docs:
                schema["description"] = arg_docs[param_name]
            if param.default is not inspect.Parameter.empty:
                schema["default"] = param.default
            else:
                required.append(param_name)
            properties[param_name] = schema

        parameters = {"type": "object", "properties": properties}
        if required:
            parameters["required"] = required

        return cls(
            func,
            name=name or func.__name__,
            description=description or _summary(func.__doc__) or func.__name__,
            parameters=parameters,
            dangerous=dangerous,
        )

    def _validate(self, arguments: dict[str, Any]) -> None:
        required = self.parameters.get("required", [])
        missing = [key for key in required if key not in arguments]
        if missing:
            raise ToolValidationError(
                f"Tool {self.name!r} missing required argument(s): {', '.join(missing)}",
                context={"tool": self.name, "missing": missing},
            )

    async def invoke(self, arguments: dict[str, Any]) -> str:
        """Validate, execute, and stringify the result.

        Note: this performs the raw call. Timeouts, allowlists, and audit are
        applied by :class:`forge.security.sandbox.ToolSandbox`, which wraps this.
        """
        self._validate(arguments)
        result = self._func(**arguments)
        if inspect.isawaitable(result):
            result = await result
        return self._coerce(result)

    @staticmethod
    def _coerce(result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, (dict, list)):
            return json.dumps(result, default=str)
        return str(result)


@overload
def tool(func: Callable[..., Any]) -> Tool: ...


@overload
def tool(
    *,
    name: str | None = ...,
    description: str | None = ...,
    dangerous: bool = ...,
) -> Callable[[Callable[..., Any]], Tool]: ...


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    dangerous: bool = False,
) -> Tool | Callable[[Callable[..., Any]], Tool]:
    """Decorator that turns a function into a :class:`Tool`.

    Usable bare (``@tool``) or with arguments
    (``@tool(name="add", dangerous=False)``).
    """

    def wrap(target: Callable[..., Any]) -> Tool:
        return Tool.from_function(target, name=name, description=description, dangerous=dangerous)

    if func is not None:
        return wrap(func)
    return wrap
