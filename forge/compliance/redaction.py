"""PII redaction.

Used to scrub common categories of personal/sensitive data from text before it
is written to logs or audit records. Pattern-based and conservative — it favours
over-redaction of obvious identifiers (emails, card numbers, SSNs) rather than
attempting exhaustive NLP-based detection.
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("IPV4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("PHONE", re.compile(r"\b(?:\+?\d{1,2}[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b")),
]


class PIIRedactor:
    """Replaces recognised PII with ``[REDACTED:KIND]`` placeholders."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def redact(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        redacted = text
        # Order matters: emails before phone/cc so an email isn't partly matched.
        for kind, pattern in _PATTERNS:
            redacted = pattern.sub(f"[REDACTED:{kind}]", redacted)
        return redacted

    def redact_obj(self, obj: Any) -> Any:
        """Recursively redact string values inside dicts/lists."""
        if not self.enabled:
            return obj
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {key: self.redact_obj(value) for key, value in obj.items()}
        if isinstance(obj, list):
            return [self.redact_obj(item) for item in obj]
        return obj
