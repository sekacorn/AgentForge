"""What a failure may say outside this process.

An exception's own text is whatever the code that raised it put there: an
upstream server's explanation, a URL, a path, a value from a request. Anything
that leaves the local log goes through :func:`public_failure` instead, and says
only the exception type and a fixed description of what failed. That covers a
prompt sent to a model, an audit record, and an event, which the OpenTelemetry
exporter ships to a collector.

The full text, with its traceback, is written to the local ``forge`` log at
WARNING, which stays on this machine and is not part of the audit trail.
"""

from __future__ import annotations

from typing import Final

from forge.observability.logging import get_logger

MODEL_CALL_FAILED: Final = "model call failed"
RUN_FAILED: Final = "run failed"
WORKER_FAILED: Final = "worker failed"

_log = get_logger("failures")


def public_failure(exc: BaseException, what: str) -> str:
    """``"<ExceptionType>: <what>"``, safe for prompts, events and audit records."""
    return f"{type(exc).__name__}: {what}"


def log_full_failure(exc: BaseException, what: str, **context: object) -> None:
    """Write the full exception, with its traceback, to the local log only."""
    _log.warning("%s (%s)", what, ", ".join(f"{k}={v}" for k, v in context.items()), exc_info=exc)
