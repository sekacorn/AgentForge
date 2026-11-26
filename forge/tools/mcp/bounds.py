"""Bounds on what an MCP server may hand to Forge, and the text a model sees.

Everything here treats server-supplied content as untrusted: schemas are
bounded and may not reach outside themselves, and any text that will reach a
model is stripped of control characters and capped. Standard library only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Final

MAX_SCHEMA_DEPTH: Final = 16
MAX_SCHEMA_BYTES: Final = 64 * 1024
MAX_SCHEMA_PROPERTIES: Final = 128
MAX_DESCRIPTION_CHARS: Final = 1024
MAX_RESULT_CHARS: Final = 16 * 1024
MAX_TOOLS_PER_SERVER: Final = 256
MAX_LIST_PAGES: Final = 16

#: The name a model sees: ``<server_id>__<tool>``. Most model APIs accept
#: ``[A-Za-z0-9_-]{1,64}``, so anything else is refused rather than rewritten.
EXPOSED_NAME: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")
SERVER_ID: Final = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_CONTROL: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")


class SchemaBoundsError(ValueError):
    """A server-supplied JSON Schema is outside the accepted bounds."""


def _walk(value: Any, depth: int) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        raise SchemaBoundsError(f"The schema is nested deeper than {MAX_SCHEMA_DEPTH} levels.")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in ("$ref", "$dynamicRef", "$recursiveRef") and (
                not isinstance(item, str) or not item.startswith("#")
            ):
                raise SchemaBoundsError("The schema references something outside itself.")
            if key == "$id":
                raise SchemaBoundsError(
                    "The schema declares its own $id, which could re-base references."
                )
            if (
                key == "properties"
                and isinstance(item, Mapping)
                and len(item) > MAX_SCHEMA_PROPERTIES
            ):
                raise SchemaBoundsError(
                    f"The schema has more than {MAX_SCHEMA_PROPERTIES} properties."
                )
            _walk(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _walk(item, depth + 1)


def check_schema(schema: object, *, what: str = "input schema") -> dict[str, Any]:
    """Return ``schema`` if it is a bounded, self-contained JSON object schema."""
    if not isinstance(schema, Mapping):
        raise SchemaBoundsError(f"The {what} is not a JSON object.")
    try:
        size = len(json.dumps(schema, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise SchemaBoundsError(f"The {what} is not plain JSON.") from None
    if size > MAX_SCHEMA_BYTES:
        raise SchemaBoundsError(f"The {what} is over {MAX_SCHEMA_BYTES // 1024} KiB.")
    _walk(schema, 1)
    return dict(schema)


def check_input_schema(schema: object) -> dict[str, Any]:
    """An input schema must also be an object schema at its root."""
    checked = check_schema(schema)
    if checked.get("type") != "object":
        raise SchemaBoundsError('The input schema must have "type": "object" at its root.')
    return checked


def model_text(text: str | None, limit: int) -> str:
    """Strip control and bidirectional-override characters, then cap at ``limit`` characters."""
    cleaned = _CONTROL.sub("", text or "")
    if len(cleaned) > limit:
        return cleaned[: limit - 1] + "…"
    return cleaned


def exposed_name(server_id: str, tool_name: str) -> str | None:
    """The name a model sees for a tool, or ``None`` when it cannot be represented."""
    name = f"{server_id}__{tool_name}"
    return name if EXPOSED_NAME.fullmatch(name) else None
