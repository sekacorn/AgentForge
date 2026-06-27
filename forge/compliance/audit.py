"""Tamper-evident audit logging.

Audit is foundational for enterprise governance (SOC 2, ISO 27001, GDPR
accountability). Forge writes an append-only JSON-Lines audit trail where each
entry is **hash-chained** to its predecessor: every record stores the SHA-256 of
``previous_hash + canonical(payload)``. Removing or editing any record breaks the
chain, which :meth:`AuditLogger.verify` detects.

Entries can be PII-redacted before being written by supplying a
:class:`~forge.compliance.redaction.PIIRedactor`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from forge.compliance.redaction import PIIRedactor
from forge.observability.logging import get_logger
from forge.types import new_id, utcnow

_log = get_logger("audit")

_GENESIS = "0" * 64


class AuditEntry(BaseModel):
    """A single, immutable audit record."""

    id: str = Field(default_factory=lambda: new_id("audit"))
    timestamp: str = Field(default_factory=lambda: utcnow().isoformat())
    actor: str
    action: str
    resource: str | None = None
    run_id: str | None = None
    outcome: str = "ok"
    region: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    previous_hash: str = _GENESIS
    hash: str = ""

    def signing_payload(self) -> str:
        """Deterministic serialization of everything except ``hash`` itself."""
        data = self.model_dump(exclude={"hash"})
        return json.dumps(data, sort_keys=True, default=str)


class AuditLogger:
    """Append-only, hash-chained audit log backed by a JSONL file."""

    def __init__(
        self,
        path: str | Path,
        *,
        enabled: bool = True,
        region: str | None = None,
        redactor: PIIRedactor | None = None,
    ) -> None:
        self.enabled = enabled
        self.path = Path(path)
        self._region = region
        self._redactor = redactor
        self._last_hash = _GENESIS
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._last_hash = self._read_last_hash()

    def record(
        self,
        action: str,
        *,
        actor: str = "system",
        resource: str | None = None,
        run_id: str | None = None,
        outcome: str = "ok",
        **metadata: Any,
    ) -> AuditEntry:
        """Append an audit entry and return it.

        When auditing is disabled the entry is constructed but not persisted, so
        callers get a consistent return type either way.
        """
        meta = dict(metadata)
        if self._redactor is not None:
            meta = self._redactor.redact_obj(meta)
            if resource is not None:
                resource = self._redactor.redact(resource)

        entry = AuditEntry(
            actor=actor,
            action=action,
            resource=resource,
            run_id=run_id,
            outcome=outcome,
            region=self._region,
            metadata=meta,
            previous_hash=self._last_hash,
        )
        entry.hash = self._compute_hash(entry)

        if self.enabled:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(entry.model_dump_json() + "\n")
            self._last_hash = entry.hash
        return entry

    def verify(self) -> bool:
        """Re-read the log and confirm the hash chain is intact."""
        if not self.path.exists():
            return True
        prev = _GENESIS
        with self.path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                entry = AuditEntry.model_validate_json(line)
                if entry.previous_hash != prev:
                    _log.error("audit chain broken at line %d (prev_hash mismatch)", line_no)
                    return False
                if entry.hash != self._compute_hash(entry):
                    _log.error("audit chain broken at line %d (hash mismatch)", line_no)
                    return False
                prev = entry.hash
        return True

    # ------------------------------------------------------------------ #
    @staticmethod
    def _compute_hash(entry: AuditEntry) -> str:
        digest = hashlib.sha256()
        digest.update(entry.previous_hash.encode("utf-8"))
        digest.update(entry.signing_payload().encode("utf-8"))
        return digest.hexdigest()

    def _read_last_hash(self) -> str:
        if not self.path.exists():
            return _GENESIS
        last = _GENESIS
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        last = AuditEntry.model_validate_json(line).hash
        except Exception:  # noqa: BLE001 - a corrupt log shouldn't crash startup
            _log.warning("could not read existing audit log; starting a new chain")
            return _GENESIS
        return last
