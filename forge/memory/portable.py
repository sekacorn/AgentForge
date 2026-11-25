"""Portable memory: list, import, delete and review records across backends.

:class:`~forge.memory.base.Memory` stays exactly as it was (``add`` / ``search``
/ ``clear``), so third-party backends keep working. Portability is a separate,
optional interface, :class:`PortableMemory`, which the three bundled backends
implement. A backend that does not implement it is simply not exportable.

Rules every backend applies, through :func:`classify_import`:

- **Imported items are untrusted.** They land ``quarantined``: stored and
  listed, never returned by ``search`` until a reviewer activates them.
- **IDs are preserved.** Re-importing an identical item is a no-op, so retries
  are safe; the same ID with different content is refused, never overwritten.
- **Deletions stick.** Deleting an item leaves a tombstone (ID and time only).
  An import never resurrects a tombstoned ID, and an item a reviewer rejected
  is not imported again.
- **Provenance cannot be forged.** Provenance is kept apart from user metadata,
  and an import carrying reserved ``_forge_*`` metadata keys is refused.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

#: The hashed bag-of-words scheme every bundled backend uses, as the AI
#: Infrastructure Suite names it.
HASHED_BOW_SCHEME: Final = "agentforge.hashed-bow.blake2b"
HASHED_BOW_REVISION: Final = "1"
#: Metadata keys an import may not carry, so a document cannot forge provenance.
RESERVED_METADATA_PREFIX: Final = "_forge_"

RecordStatus = Literal["active", "quarantined"]
ImportOutcome = Literal[
    "imported", "unchanged", "conflict", "tombstoned", "previously_rejected", "reserved_metadata"
]


class ImportSource(BaseModel):
    """One import run: where its records came from, and who imported them."""

    source_system: str
    source_backend: str
    source_store_id: str
    export_id: str
    export_sha256: str
    imported_at: datetime
    importer_tool: str
    importer_version: str

    def for_record(self, original_item_id: str) -> ImportProvenance:
        return ImportProvenance(**self.model_dump(), original_item_id=original_item_id)


class ImportProvenance(ImportSource):
    """Where one stored record came from. Kept apart from user metadata."""

    original_item_id: str


class MemoryRecord(BaseModel):
    """One stored item with everything a portable export needs."""

    item_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    status: RecordStatus = "active"
    provenance: ImportProvenance | None = None


class Tombstone(BaseModel):
    """An item deleted under policy: an ID and a time, never content."""

    item_id: str
    deleted_at: datetime


class RecordPage(BaseModel):
    """A page of records ordered by ``item_id``; pass ``next_after`` to continue."""

    records: list[MemoryRecord]
    next_after: str | None = None


class ImportReport(BaseModel):
    """What an import did with each record it received."""

    received: int = 0
    imported: int = 0
    unchanged: int = 0
    refused: dict[str, ImportOutcome] = Field(default_factory=dict)

    @property
    def refused_count(self) -> int:
        return len(self.refused)


class ReviewReport(BaseModel):
    """What a review decision changed."""

    activated: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    not_quarantined: list[str] = Field(default_factory=list)


class EmbeddingScheme(BaseModel):
    """The scheme, revision and dimension a backend embeds with."""

    scheme_id: str
    revision: str
    dimensions: int


@runtime_checkable
class PortableMemory(Protocol):
    """Optional interface for backends whose contents can leave and arrive intact."""

    async def list_records(self, *, after: str | None = None, limit: int = 1000) -> RecordPage:
        """Records (active and quarantined) with ``item_id > after``, ordered by ``item_id``."""
        ...

    async def list_tombstones(self) -> list[Tombstone]:
        """Every tombstone, ordered by ``item_id``."""
        ...

    async def import_records(
        self, records: Sequence[MemoryRecord], *, source: ImportSource
    ) -> ImportReport:
        """Import records as ``quarantined``, preserving IDs (see module rules).

        A record's own ``status`` and ``provenance`` are ignored: every imported
        record is quarantined, with provenance from ``source``.
        """
        ...

    async def delete_record(self, item_id: str, *, reason: str) -> Tombstone:
        """Delete an item under policy, leaving a tombstone. Idempotent."""
        ...

    async def review_records(
        self, item_ids: Sequence[str], *, decision: Literal["activate", "reject"], reviewer: str
    ) -> ReviewReport:
        """Activate quarantined items, or reject them (removed, never re-imported)."""
        ...

    @property
    def embedding_scheme(self) -> EmbeddingScheme:
        """How this backend embeds text."""
        ...


def content_key(text: str, metadata: Mapping[str, Any]) -> str:
    """A canonical form for comparing an imported record with a stored one."""
    return json.dumps(
        {"text": text, "metadata": dict(metadata)}, sort_keys=True, separators=(",", ":")
    )


def has_reserved_metadata(metadata: Mapping[str, Any]) -> bool:
    return any(str(key).startswith(RESERVED_METADATA_PREFIX) for key in metadata)


def classify_import(
    record: MemoryRecord,
    *,
    existing: tuple[str, Mapping[str, Any]] | None,
    tombstoned: bool,
    rejected: bool,
) -> ImportOutcome:
    """The one decision every backend makes for an incoming record."""
    if tombstoned:
        return "tombstoned"
    if rejected:
        return "previously_rejected"
    if has_reserved_metadata(record.metadata):
        return "reserved_metadata"
    if existing is not None:
        same = content_key(*existing) == content_key(record.text, record.metadata)
        return "unchanged" if same else "conflict"
    return "imported"


def page(records: Sequence[MemoryRecord], *, after: str | None, limit: int) -> RecordPage:
    """Slice an ``item_id``-ordered sequence into a page."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    selected = [r for r in records if after is None or r.item_id > after][: limit + 1]
    more = len(selected) > limit
    selected = selected[:limit]
    return RecordPage(records=selected, next_after=selected[-1].item_id if more else None)
