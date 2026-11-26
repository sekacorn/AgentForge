"""Detect credentials in text before it leaves the process.

Used by memory export: an item that still looks like it holds a secret stops
the export, and the error names the item and the kind of secret, never the
value. Pattern-based and conservative: it looks for formats that are almost
never anything but a credential, so a false positive is rare and a deliberate
redaction step resolves it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Final

#: (kind, pattern). Kinds are descriptive, never vendor names.
_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
    ("cloud_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("api_key_sk_prefix", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    ("source_host_token", re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{30,}")),
    ("chat_workspace_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    (
        "json_web_token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ),
    ("bearer_header", re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    (
        "url_credentials",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),
    ),
)


def secret_kinds(text: str) -> list[str]:
    """The kinds of credential that appear in ``text``, in a fixed order."""
    return [kind for kind, pattern in _PATTERNS if pattern.search(text)]


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def secret_kinds_in(*values: Any) -> list[str]:
    """Secret kinds found anywhere in strings, or nested dicts and lists of them."""
    found: list[str] = []
    for value in values:
        for text in _strings(value):
            for kind in secret_kinds(text):
                if kind not in found:
                    found.append(kind)
    return found


def redact_secrets(text: str) -> str:
    """Replace every detected credential with ``[REDACTED:<kind>]``."""
    for kind, pattern in _PATTERNS:
        text = pattern.sub(f"[REDACTED:{kind}]", text)
    return text


def redact_secrets_in(value: Any) -> Any:
    """``redact_secrets`` over strings, or nested dicts and lists of them."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: redact_secrets_in(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets_in(item) for item in value]
    return value
