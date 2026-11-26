"""Reviewing imported memory before it becomes active (OWASP ASI06).

Imported items arrive ``quarantined``. :func:`list_quarantine` shows them with
the injection indicators :class:`~forge.security.sanitization.InputSanitizer`
finds in their text and metadata (never the text itself). :func:`review_items`
applies a decision:

- **activate**: an unflagged item becomes searchable. A *flagged* item is
  activated only with ``accept_flagged=True`` and only one item at a time; a
  bulk activation skips flagged items and reports them.
- **reject**: the item is removed and will never be imported again.

Each decision can be recorded in Forge's hash-chained audit log as
``agentforge.memory.review``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from forge.compliance.audit import AuditLogger
from forge.exceptions import MemoryBackendError
from forge.memory.portable import MemoryRecord, PortableMemory
from forge.security.sanitization import InputSanitizer

REVIEW_ACTION = "agentforge.memory.review"


@dataclass(frozen=True)
class QuarantineEntry:
    """A quarantined item, described without its text."""

    item_id: str
    export_id: str | None
    source_system: str | None
    flags: tuple[str, ...]


@dataclass
class ReviewOutcome:
    """What a review changed, and what it left alone and why."""

    activated: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    skipped_flagged: dict[str, tuple[str, ...]] = field(default_factory=dict)
    not_quarantined: list[str] = field(default_factory=list)


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


def flags(record: MemoryRecord, sanitizer: InputSanitizer | None = None) -> tuple[str, ...]:
    """Injection indicators found in a record's text or metadata."""
    sanitizer = sanitizer or InputSanitizer()
    found: list[str] = []
    for text in (record.text, *_strings(record.metadata)):
        for reason in sanitizer.scan(text).reasons:
            if reason not in found:
                found.append(reason)
    return tuple(found)


async def _quarantined(store: PortableMemory) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    after: str | None = None
    while True:
        page = await store.list_records(after=after, limit=1000)
        records.extend(r for r in page.records if r.status == "quarantined")
        if page.next_after is None:
            return records
        after = page.next_after


async def list_quarantine(store: PortableMemory) -> list[QuarantineEntry]:
    """Every quarantined item, with its source and any injection indicators."""
    sanitizer = InputSanitizer()
    return [
        QuarantineEntry(
            item_id=record.item_id,
            export_id=None if record.provenance is None else record.provenance.export_id,
            source_system=None if record.provenance is None else record.provenance.source_system,
            flags=flags(record, sanitizer),
        )
        for record in await _quarantined(store)
    ]


async def review_items(
    store: PortableMemory,
    item_ids: Sequence[str] | None = None,
    *,
    decision: Literal["activate", "reject"],
    reviewer: str,
    from_export: str | None = None,
    accept_flagged: bool = False,
    audit: AuditLogger | None = None,
) -> ReviewOutcome:
    """Activate or reject quarantined items, named or all from one export."""
    if not reviewer.strip():
        raise MemoryBackendError("A review needs a reviewer name.")
    if (item_ids is None) == (from_export is None):
        raise MemoryBackendError("Name the items to review, or the export they came from.")
    quarantined = {record.item_id: record for record in await _quarantined(store)}
    if from_export is not None:
        targets = [
            item_id
            for item_id, record in quarantined.items()
            if record.provenance is not None and record.provenance.export_id == from_export
        ]
    else:
        targets = list(dict.fromkeys(item_ids or []))
    if accept_flagged and (decision != "activate" or len(targets) != 1):
        raise MemoryBackendError("accept_flagged activates exactly one item at a time.")

    outcome = ReviewOutcome()
    to_apply: list[str] = []
    sanitizer = InputSanitizer()
    for item_id in targets:
        record = quarantined.get(item_id)
        if record is None:
            outcome.not_quarantined.append(item_id)
            continue
        found = flags(record, sanitizer)
        if decision == "activate" and found and not accept_flagged:
            outcome.skipped_flagged[item_id] = found
            continue
        to_apply.append(item_id)
    if to_apply:
        applied = await store.review_records(to_apply, decision=decision, reviewer=reviewer)
        outcome.activated.extend(applied.activated)
        outcome.rejected.extend(applied.rejected)
        outcome.not_quarantined.extend(applied.not_quarantined)
    if audit is not None:
        for item_id in [*outcome.activated, *outcome.rejected]:
            record = quarantined[item_id]
            audit.record(
                REVIEW_ACTION,
                actor=reviewer,
                resource=item_id,
                outcome="activated" if item_id in outcome.activated else "rejected",
                export_id=None if record.provenance is None else record.provenance.export_id,
                flags=list(flags(record, sanitizer)),
            )
    return outcome
