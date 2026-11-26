"""An in-memory vector store for retrieval-augmented generation (RAG).

This bundled store needs no external services and no embedding API. It uses a
deterministic, hashing-based bag-of-words embedding with cosine similarity. That
is enough to demonstrate end-to-end RAG offline and to power tests; for
production semantic search, implement :class:`~forge.memory.base.Memory` over a
real embedding model and vector database — the interface is identical, so agents
don't change.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from forge.memory.base import Memory, MemoryItem
from forge.memory.portable import (
    HASHED_BOW_REVISION,
    HASHED_BOW_SCHEME,
    EmbeddingScheme,
    ImportProvenance,
    ImportReport,
    ImportSource,
    MemoryRecord,
    RecordPage,
    RecordStatus,
    ReviewReport,
    Tombstone,
    classify_import,
    page,
)
from forge.types import utcnow

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _stable_bucket(token: str, dim: int) -> int:
    """Map a token to a bucket with a process-independent hash.

    Python's built-in ``hash`` is salted per process (``PYTHONHASHSEED``), which
    would make embeddings differ between runs. A fixed digest keeps the store
    genuinely deterministic and reproducible.
    """
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dim


@dataclass
class _ItemState:
    """What the in-memory store keeps beside each item for portability."""

    created_at: datetime | None
    status: RecordStatus = "active"
    provenance: ImportProvenance | None = None


class InMemoryVectorStore(Memory):
    """A simple cosine-similarity store over hashed bag-of-words vectors.

    Implements :class:`~forge.memory.portable.PortableMemory`.
    """

    def __init__(self, *, dim: int = 512) -> None:
        self._dim = dim
        self._items: list[MemoryItem] = []
        self._vectors: list[list[float]] = []
        self._state: dict[str, _ItemState] = {}
        self._tombstones: dict[str, Tombstone] = {}
        self._rejected: set[str] = set()

    def _embed(self, text: str) -> list[float]:
        """Hash tokens into a fixed-dimension, L2-normalized term-frequency vector."""
        vector = [0.0] * self._dim
        for token in _tokenize(text):
            vector[_stable_bucket(token, self._dim)] += 1.0
        norm = math.sqrt(sum(component * component for component in vector))
        if norm > 0:
            vector = [component / norm for component in vector]
        return vector

    async def add(self, text: str, *, metadata: dict[str, object] | None = None) -> str:
        item = MemoryItem(text=text, metadata=metadata or {})
        self._items.append(item)
        self._vectors.append(self._embed(text))
        self._state[item.id] = _ItemState(created_at=utcnow())
        return item.id

    async def search(self, query: str, *, k: int = 5) -> list[MemoryItem]:
        if not self._items:
            return []
        query_vector = self._embed(query)
        scored: list[MemoryItem] = []
        for item, vector in zip(self._items, self._vectors, strict=True):
            if self._status(item.id) != "active":
                continue  # quarantined imports never reach a search
            score = sum(a * b for a, b in zip(query_vector, vector, strict=True))
            scored.append(item.model_copy(update={"score": round(score, 6)}))
        scored.sort(key=lambda item: item.score or 0.0, reverse=True)
        return [item for item in scored[:k] if (item.score or 0.0) > 0.0]

    async def clear(self) -> None:
        # Tombstones and rejections are policy, not content, so they survive.
        self._items.clear()
        self._vectors.clear()
        self._state.clear()

    def __len__(self) -> int:
        return len(self._items)

    # --- PortableMemory ------------------------------------------------------------

    def _status(self, item_id: str) -> RecordStatus:
        state = self._state.get(item_id)
        return state.status if state is not None else "active"

    def _index(self, item_id: str) -> int | None:
        for index, item in enumerate(self._items):
            if item.id == item_id:
                return index
        return None

    def _remove(self, item_id: str) -> None:
        index = self._index(item_id)
        if index is not None:
            del self._items[index]
            del self._vectors[index]
        self._state.pop(item_id, None)

    @property
    def embedding_scheme(self) -> EmbeddingScheme:
        return EmbeddingScheme(
            scheme_id=HASHED_BOW_SCHEME, revision=HASHED_BOW_REVISION, dimensions=self._dim
        )

    async def list_records(self, *, after: str | None = None, limit: int = 1000) -> RecordPage:
        records = []
        for item in self._items:
            state = self._state.get(item.id)
            records.append(
                MemoryRecord(
                    item_id=item.id,
                    text=item.text,
                    metadata=dict(item.metadata),
                    created_at=None if state is None else state.created_at,
                    status="active" if state is None else state.status,
                    provenance=None if state is None else state.provenance,
                )
            )
        records.sort(key=lambda record: record.item_id)
        return page(records, after=after, limit=limit)

    async def list_tombstones(self) -> list[Tombstone]:
        return [self._tombstones[key] for key in sorted(self._tombstones)]

    async def import_records(
        self, records: Sequence[MemoryRecord], *, source: ImportSource
    ) -> ImportReport:
        report = ImportReport(received=len(records))
        for record in records:
            index = self._index(record.item_id)
            existing = None if index is None else self._items[index]
            outcome = classify_import(
                record,
                existing=None if existing is None else (existing.text, existing.metadata),
                tombstoned=record.item_id in self._tombstones,
                rejected=record.item_id in self._rejected,
            )
            if outcome == "unchanged":
                report.unchanged += 1
            elif outcome != "imported":
                report.refused[record.item_id] = outcome
            else:
                self._items.append(
                    MemoryItem(id=record.item_id, text=record.text, metadata=dict(record.metadata))
                )
                self._vectors.append(self._embed(record.text))
                self._state[record.item_id] = _ItemState(
                    created_at=record.created_at,
                    status="quarantined",
                    provenance=source.for_record(record.item_id),
                )
                report.imported += 1
        return report

    async def delete_record(self, item_id: str, *, reason: str) -> Tombstone:
        if item_id in self._tombstones:
            return self._tombstones[item_id]
        self._remove(item_id)
        tombstone = Tombstone(item_id=item_id, deleted_at=utcnow())
        self._tombstones[item_id] = tombstone
        return tombstone

    async def review_records(
        self, item_ids: Sequence[str], *, decision: Literal["activate", "reject"], reviewer: str
    ) -> ReviewReport:
        report = ReviewReport()
        for item_id in item_ids:
            state = self._state.get(item_id)
            if state is None or state.status != "quarantined":
                report.not_quarantined.append(item_id)
            elif decision == "activate":
                state.status = "active"
                report.activated.append(item_id)
            else:
                self._remove(item_id)
                self._rejected.add(item_id)
                report.rejected.append(item_id)
        return report
