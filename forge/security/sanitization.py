"""Input sanitization and prompt-injection heuristics.

This is defense-in-depth, not a silver bullet. The goal is to (a) normalize and
bound untrusted input before it reaches a model, and (b) flag the most common
prompt-injection patterns so an operator can decide policy. Detection is
heuristic and deliberately conservative; it never silently rewrites a user's
intent — it either passes the text through or raises so the caller can react.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from forge.config import SecurityConfig
from forge.exceptions import PromptInjectionError, SecurityError

# Control characters are stripped except for tab/newline/carriage-return.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# A small, high-signal set of injection indicators. Kept readable on purpose so
# operators can audit and extend it.
_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("override_instructions", re.compile(r"\bignore\s+(all\s+)?(previous|prior|above)\b", re.I)),
    ("disregard_instructions", re.compile(r"\bdisregard\s+(the\s+)?(above|previous|system)\b", re.I)),
    ("reveal_system_prompt", re.compile(r"\b(reveal|print|show|repeat)\b.{0,30}\b(system\s+prompt|instructions)\b", re.I)),
    ("role_override", re.compile(r"\byou\s+are\s+now\b", re.I)),
    ("exfiltrate_secrets", re.compile(r"\b(api[_\s-]?key|password|secret|token)s?\b.{0,20}\b(reveal|send|print|leak)\b", re.I)),
    ("developer_mode", re.compile(r"\b(developer|god|jailbreak|DAN)\s+mode\b", re.I)),
]


class SanitizationResult(BaseModel):
    """Outcome of scanning a piece of text."""

    text: str
    flagged: bool = False
    reasons: list[str] = Field(default_factory=list)


class InputSanitizer:
    """Normalizes input and screens it for prompt-injection indicators."""

    def __init__(self, config: SecurityConfig | None = None) -> None:
        self._config = config or SecurityConfig()

    def sanitize(self, text: str) -> str:
        """Strip control characters and enforce the configured length bound."""
        cleaned = _CONTROL_CHARS.sub("", text)
        if len(cleaned) > self._config.max_input_chars:
            raise SecurityError(
                "Input exceeds the maximum allowed length",
                context={"limit": self._config.max_input_chars, "length": len(cleaned)},
            )
        return cleaned

    def scan(self, text: str) -> SanitizationResult:
        """Return a result flagging any matched injection indicators."""
        reasons = [name for name, pattern in _INJECTION_PATTERNS if pattern.search(text)]
        return SanitizationResult(text=text, flagged=bool(reasons), reasons=reasons)

    def check(self, text: str) -> str:
        """Sanitize, then (if enabled) raise on prompt-injection indicators.

        Returns the sanitized text when it passes.
        """
        cleaned = self.sanitize(text)
        if not self._config.detect_prompt_injection:
            return cleaned
        result = self.scan(cleaned)
        if result.flagged:
            raise PromptInjectionError(
                "Potential prompt injection detected in input",
                context={"reasons": result.reasons},
            )
        return cleaned
