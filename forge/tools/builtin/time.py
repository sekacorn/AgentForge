"""A trivial, safe time tool."""

from __future__ import annotations

from forge.tools.base import tool
from forge.types import utcnow


@tool
def utc_now() -> str:
    """Return the current UTC date and time in ISO-8601 format."""
    return utcnow().isoformat()
