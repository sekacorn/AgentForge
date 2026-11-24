"""Describing an upstream failure without repeating what the server said.

An upstream error body is untrusted text. A server, or a proxy in front of it,
can reflect request headers into it, and the ``Authorization`` header is the
API key. So provider errors never interpolate upstream text into a message,
and are raised ``from None`` so the original exception, which carries that
text, cannot be rendered through the cause chain either. What an error does
carry is built here: values this library chose or measured.
"""

from __future__ import annotations

from typing import Any


def error_context(model: str, exc: BaseException) -> dict[str, Any]:
    """The model, the exception type and, when there is one, the HTTP status."""
    context: dict[str, Any] = {"model": model, "error_type": type(exc).__name__}
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        context["status_code"] = status
    return context
