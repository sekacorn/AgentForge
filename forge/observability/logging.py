"""Structured logging for Forge.

Two output modes:

* **Human** (default) — pretty, colorized output via ``rich`` when available.
* **JSON** — one JSON object per line, suitable for log shippers / SIEMs.

Use :func:`configure_logging` once at startup and :func:`get_logger` everywhere
else. Loggers are namespaced under ``forge.*``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

_CONFIGURED = False
_LOGGER_NAMESPACE = "forge"


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON for machine consumption."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Attach any structured ``extra`` fields passed via ``logger.info(..., extra=...)``.
        for key, value in record.__dict__.get("forge_extra", {}).items():
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", *, json_logs: bool = False) -> None:
    """Configure the ``forge`` logger namespace.

    Idempotent: calling it again replaces the handler so tests and repeated
    ``Orchestrator`` construction don't stack duplicate handlers.
    """
    global _CONFIGURED

    logger = logging.getLogger(_LOGGER_NAMESPACE)
    logger.setLevel(level.upper())
    logger.handlers.clear()
    logger.propagate = False

    handler: logging.Handler
    if json_logs:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
    else:
        try:
            from rich.logging import RichHandler

            handler = RichHandler(
                rich_tracebacks=True,
                show_path=False,
                markup=False,
                log_time_format="[%X]",
            )
        except Exception:  # pragma: no cover - rich is a hard dep, but stay safe
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
            )

    logger.addHandler(handler)
    _CONFIGURED = True


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger under the ``forge`` namespace, configuring on first use."""
    if not _CONFIGURED:
        configure_logging()
    if name is None:
        return logging.getLogger(_LOGGER_NAMESPACE)
    if name.startswith(_LOGGER_NAMESPACE):
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_NAMESPACE}.{name}")
