"""Regression test: audit-log verification must fail closed on a tampered line.

A line that no longer parses is itself evidence of tampering; ``verify()`` should
return ``False`` (its documented contract) rather than raising a ValidationError.
"""

from __future__ import annotations

from forge.compliance.audit import AuditLogger


def test_verify_returns_false_on_unparseable_line(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(path)
    logger.record("run.start", actor="system")
    logger.record("run.finish", actor="system")
    assert logger.verify() is True

    # Tamper by appending a garbage (non-JSON) line.
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json at all\n")

    assert logger.verify() is False
