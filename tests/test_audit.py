from __future__ import annotations

import json

from forge import AuditLogger, PIIRedactor


def test_audit_chain_verifies(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLogger(path)
    log.record("run.start", actor="alice")
    log.record("tool.call", actor="agent", resource="calculator")
    assert log.verify() is True


def test_tampering_breaks_the_chain(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLogger(path)
    log.record("a", actor="x")
    log.record("b", actor="y")
    assert log.verify() is True

    # Edit a record in place without recomputing its hash.
    lines = path.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[0])
    obj["actor"] = "mallory"
    lines[0] = json.dumps(obj)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert AuditLogger(path).verify() is False


def test_pii_is_redacted_before_write(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLogger(path, redactor=PIIRedactor(enabled=True))
    log.record("note", actor="sys", detail="reach me at alice@example.com")
    content = path.read_text(encoding="utf-8")
    assert "alice@example.com" not in content
    assert "REDACTED:EMAIL" in content


def test_disabled_audit_does_not_write(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLogger(path, enabled=False)
    entry = log.record("x", actor="sys")
    assert entry.action == "x"
    assert not path.exists()
