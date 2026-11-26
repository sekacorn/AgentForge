"""Canonical JSON (RFC 8785) and MCP tool definition hashes.

A tool's **definition hash** is the SHA-256 of the RFC 8785 (JSON
Canonicalization Scheme) serialization of::

    {"server_id", "name", "description", "inputSchema", "outputSchema", "annotations"}

with absent members as ``null``. It is what an approval pins: change any of
those six and the hash changes, so the tool is refused until re-approved.
``title``, ``icons`` and ``_meta`` are deliberately outside the hash; nothing in
them is ever shown to a model.

Standard library only, so approvals can be read and checked without the
optional ``mcp`` SDK installed.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Final

HASHED_FIELDS: Final = (
    "server_id",
    "name",
    "description",
    "inputSchema",
    "outputSchema",
    "annotations",
)


class CanonicalizationError(ValueError):
    """A value cannot be represented in RFC 8785 canonical JSON."""


def _number(value: int | float) -> str:
    """ECMAScript ``Number.prototype.toString``, as RFC 8785 section 3.2.2.3 requires."""
    if isinstance(value, bool):  # pragma: no cover - guarded by the caller
        raise CanonicalizationError("A boolean is not a number.")
    if isinstance(value, int):
        if abs(value) > 2**53 - 1:
            raise CanonicalizationError(
                "An integer outside the IEEE 754 safe range cannot be canonicalized."
            )
        return str(value)
    if not math.isfinite(value):
        raise CanonicalizationError("NaN and Infinity have no JSON representation.")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    # repr gives the shortest digits that round-trip, which is what ECMAScript uses.
    _, digit_tuple, exponent = Decimal(repr(abs(value))).as_tuple()
    digits = "".join(str(d) for d in digit_tuple).rstrip("0") or "0"
    assert isinstance(exponent, int)  # finite, checked above  # nosec B101
    k = len(digits)
    # ECMAScript's n: the value is digits x 10^(n - k).
    n = exponent + len(digit_tuple)
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    power = n - 1
    mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
    return f"{sign}{mantissa}e{'+' if power > 0 else '-'}{abs(power)}"


def _string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _utf16_key(key: str) -> list[int]:
    encoded = key.encode("utf-16-be")
    return [int.from_bytes(encoded[i : i + 2], "big") for i in range(0, len(encoded), 2)]


def canonical_json(value: Any) -> str:
    """Serialize ``value`` as RFC 8785 canonical JSON."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int | float):
        return _number(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list | tuple):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise CanonicalizationError("Object keys must be strings.")
        items = sorted(value.items(), key=lambda item: _utf16_key(item[0]))
        return "{" + ",".join(f"{_string(key)}:{canonical_json(item)}" for key, item in items) + "}"
    raise CanonicalizationError(f"A {type(value).__name__} has no JSON representation.")


def definition_document(
    server_id: str,
    name: str,
    *,
    description: str | None,
    input_schema: Mapping[str, Any],
    output_schema: Mapping[str, Any] | None = None,
    annotations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The exact object a definition hash covers."""
    return {
        "server_id": server_id,
        "name": name,
        "description": description,
        "inputSchema": dict(input_schema),
        "outputSchema": None if output_schema is None else dict(output_schema),
        "annotations": None if annotations is None else dict(annotations),
    }


def definition_sha256(
    server_id: str,
    name: str,
    *,
    description: str | None,
    input_schema: Mapping[str, Any],
    output_schema: Mapping[str, Any] | None = None,
    annotations: Mapping[str, Any] | None = None,
) -> str:
    """SHA-256 (hex) of the RFC 8785 serialization of a tool's definition."""
    document = definition_document(
        server_id,
        name,
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        annotations=annotations,
    )
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()
