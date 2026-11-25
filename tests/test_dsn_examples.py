"""Examples never show a database password inside a DSN.

A DSN ends up in shell history, process listings and configuration dumps, so
the examples name only user, host, port and database, and point to PGPASSWORD
or a .pgpass file for the password.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from forge.config import ForgeConfig
from forge.exceptions import ConfigurationError
from forge.memory import build_memory

ROOT = Path(__file__).resolve().parents[1]
#: scheme://user:password@ — any userinfo that carries a password.
INLINE_PASSWORD = re.compile(r"postgres(?:ql)?://[^\s/@:\"']+:[^\s/@\"']+@")


def _example_files() -> list[Path]:
    return sorted([*(ROOT / "forge").rglob("*.py"), *(ROOT / "docs" / "guides").rglob("*.md")])


def test_no_example_dsn_carries_a_password() -> None:
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in _example_files()
        if INLINE_PASSWORD.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_pattern_recognises_an_inline_password() -> None:
    assert INLINE_PASSWORD.search("postgresql://user:secret@host/db")
    assert not INLINE_PASSWORD.search("postgresql://forge@db-host:5432/forge")


def test_the_missing_dsn_error_points_to_pgpassword(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORGE_PGVECTOR_DSN", raising=False)
    config = ForgeConfig(memory_backend="pgvector")
    with pytest.raises(ConfigurationError) as raised:
        build_memory(config)
    message = str(raised.value)
    assert "PGPASSWORD" in message
    assert not INLINE_PASSWORD.search(message)
