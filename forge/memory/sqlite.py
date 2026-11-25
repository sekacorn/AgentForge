"""A SQLite-backed durable memory store for retrieval-augmented generation (RAG).

Unlike :class:`~forge.memory.vector.InMemoryVectorStore`, this backend persists
documents to a SQLite database file, so RAG state survives process restarts. It
needs no external vector extension (sqlite-vss / sqlite-vec): embeddings are the
same deterministic hashing bag-of-words vectors used by the in-memory store, and
cosine similarity is computed in pure Python. That keeps the install light while
delivering real persistence.

Async I/O goes through ``aiosqlite``, an *optional* dependency imported lazily so
the core library and the default in-memory store work without it. Install with::

    pip install "agentforge-oss[sqlite]"

Use it as an async context manager so the connection is opened and closed cleanly::

    async with SQLiteMemoryStore("memory.db") as store:
        await store.add("a fact to remember")
        hits = await store.search("a query")
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from forge.exceptions import MemoryDimensionError
from forge.memory.base import Memory, MemoryItem, dimension_mismatch_message
from forge.memory.portable import (
    HASHED_BOW_REVISION,
    HASHED_BOW_SCHEME,
    EmbeddingScheme,
    ImportProvenance,
    ImportReport,
    ImportSource,
    MemoryRecord,
    RecordPage,
    ReviewReport,
    Tombstone,
    classify_import,
)
from forge.memory.vector import _stable_bucket, _tokenize
from forge.types import new_id, utcnow

if TYPE_CHECKING:
    import aiosqlite

_AIOSQLITE_MISSING = (
    "SQLite memory backend requires aiosqlite: pip install 'agentforge-oss[sqlite]'"
)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    embedding TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL
)
"""

# Additive migrations for portability (agentforge-oss 0.5.9). Existing rows are
# active and native: the defaults say exactly that. Nothing is dropped or renamed.
_ADDED_COLUMNS = (
    ("status", "TEXT NOT NULL DEFAULT 'active'"),
    ("provenance", "TEXT"),
)
_CREATE_PORTABILITY_SQL = (
    "CREATE TABLE IF NOT EXISTS memory_tombstones ("
    "item_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL, reason TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS memory_rejected ("
    "item_id TEXT PRIMARY KEY, rejected_at TEXT NOT NULL, reviewer TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS forge_memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)
#: ``created_at`` is NOT NULL in the original table; an imported item whose
#: source never recorded a time is stored with this marker and read back as None.
_UNKNOWN_TIME = ""


def _time(value: str) -> datetime | None:
    return None if value == _UNKNOWN_TIME else datetime.fromisoformat(value)


class SQLiteMemoryStore(Memory):
    """A durable cosine-similarity store backed by a SQLite database file."""

    def __init__(self, path: str | Path = "forge_memory.db", *, embedding_dim: int = 64) -> None:
        self._path = str(path)
        self._dim = embedding_dim
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    def _embed(self, text: str) -> list[float]:
        """Hash tokens into a fixed-dimension, L2-normalized term-frequency vector.

        Identical approach to :class:`InMemoryVectorStore` so retrieval behaves the
        same and tests stay hermetic (no embedding API).
        """
        vector = [0.0] * self._dim
        for token in _tokenize(text):
            vector[_stable_bucket(token, self._dim)] += 1.0
        norm = math.sqrt(sum(component * component for component in vector))
        if norm > 0:
            vector = [component / norm for component in vector]
        return vector

    async def __aenter__(self) -> SQLiteMemoryStore:
        await self._ensure_conn()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _ensure_conn(self) -> aiosqlite.Connection:
        """Open the connection (and create the schema) on first use.

        Guarded by a lock so that concurrent first use opens exactly one
        connection instead of racing to create (and leak) duplicates.
        """
        if self._conn is not None:
            return self._conn
        async with self._lock:
            if self._conn is not None:
                return self._conn
            try:
                import aiosqlite
            except ImportError as exc:
                raise ImportError(_AIOSQLITE_MISSING) from exc
            conn = await aiosqlite.connect(self._path)
            await conn.execute(_CREATE_TABLE_SQL)
            await self._migrate(conn)
            await conn.commit()
            # A store built at another dimension is refused before it can be
            # searched or have vectors of a second dimension written into it.
            cursor = await conn.execute("SELECT embedding FROM memories LIMIT 1")
            row = await cursor.fetchone()
            await cursor.close()
            if row is not None:
                stored = len(json.loads(row[0]))
                if stored != self._dim:
                    await conn.close()
                    raise MemoryDimensionError(
                        dimension_mismatch_message("SQLite", stored, self._dim)
                    )
            # The store records the dimension it was built with, so even an
            # empty store refuses to be reopened at another one.
            recorded = await self._meta(conn, "dimensions")
            if recorded is None:
                await self._record_scheme(conn)
            elif int(recorded) != self._dim:
                await conn.close()
                raise MemoryDimensionError(
                    dimension_mismatch_message("SQLite", int(recorded), self._dim)
                )
            self._conn = conn
            return conn

    @staticmethod
    async def _migrate(conn: aiosqlite.Connection) -> None:
        cursor = await conn.execute("PRAGMA table_info(memories)")
        present = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        for column, definition in _ADDED_COLUMNS:
            if column not in present:
                # Fixed names and definitions from this module, never input.
                await conn.execute(f"ALTER TABLE memories ADD COLUMN {column} {definition}")  # nosec B608
        for statement in _CREATE_PORTABILITY_SQL:
            await conn.execute(statement)

    @staticmethod
    async def _meta(conn: aiosqlite.Connection, key: str) -> str | None:
        cursor = await conn.execute("SELECT value FROM forge_memory_meta WHERE key = ?", (key,))
        row = await cursor.fetchone()
        await cursor.close()
        return None if row is None else str(row[0])

    async def _record_scheme(self, conn: aiosqlite.Connection) -> None:
        for key, value in (
            ("scheme_id", HASHED_BOW_SCHEME),
            ("revision", HASHED_BOW_REVISION),
            ("dimensions", str(self._dim)),
        ):
            await conn.execute(
                "INSERT OR IGNORE INTO forge_memory_meta (key, value) VALUES (?, ?)", (key, value)
            )
        await conn.commit()

    async def add(self, text: str, *, metadata: dict[str, object] | None = None) -> str:
        conn = await self._ensure_conn()
        item_id = new_id("mem")
        embedding = json.dumps(self._embed(text))
        meta = json.dumps(metadata) if metadata is not None else None
        await conn.execute(
            "INSERT INTO memories (id, text, embedding, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (item_id, text, embedding, meta, utcnow().isoformat()),
        )
        await conn.commit()
        return item_id

    async def search(self, query: str, *, k: int = 5) -> list[MemoryItem]:
        conn = await self._ensure_conn()
        query_vector = self._embed(query)
        # Full table scan + in-Python cosine similarity. This is fine for the
        # modest stores this backend targets; for large corpora a native vector
        # extension (sqlite-vec) or an ANN index would scale retrieval far better.
        # Quarantined imports never reach a search.
        cursor = await conn.execute(
            "SELECT id, text, embedding, metadata FROM memories WHERE status = 'active'"
        )
        rows = await cursor.fetchall()
        await cursor.close()

        scored: list[MemoryItem] = []
        for row in rows:
            vector: list[float] = json.loads(row[2])
            if len(vector) != len(query_vector):
                # A table written by an earlier version can hold two dimensions.
                raise MemoryDimensionError(
                    dimension_mismatch_message("SQLite", len(vector), len(query_vector))
                )
            score = sum(a * b for a, b in zip(query_vector, vector, strict=True))
            raw_metadata = row[3]
            metadata: dict[str, object] = json.loads(raw_metadata) if raw_metadata else {}
            scored.append(
                MemoryItem(id=row[0], text=row[1], metadata=metadata, score=round(score, 6))
            )
        scored.sort(key=lambda item: item.score or 0.0, reverse=True)
        return scored[:k]

    async def clear(self) -> None:
        conn = await self._ensure_conn()
        await conn.execute("DELETE FROM memories")
        await conn.commit()

    async def aclose(self) -> None:
        """Close the underlying connection. Safe to call more than once."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # --- PortableMemory ------------------------------------------------------------

    @property
    def embedding_scheme(self) -> EmbeddingScheme:
        return EmbeddingScheme(
            scheme_id=HASHED_BOW_SCHEME, revision=HASHED_BOW_REVISION, dimensions=self._dim
        )

    async def list_records(self, *, after: str | None = None, limit: int = 1000) -> RecordPage:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT id, text, metadata, created_at, status, provenance FROM memories "
            "WHERE (? IS NULL OR id > ?) ORDER BY id LIMIT ?",
            (after, after, limit + 1),
        )
        rows = list(await cursor.fetchall())
        await cursor.close()
        records = [
            MemoryRecord(
                item_id=row[0],
                text=row[1],
                metadata=json.loads(row[2]) if row[2] else {},
                created_at=_time(row[3]),
                status=row[4],
                provenance=None if row[5] is None else ImportProvenance.model_validate_json(row[5]),
            )
            for row in rows[:limit]
        ]
        more = len(rows) > limit
        return RecordPage(records=records, next_after=records[-1].item_id if more else None)

    async def list_tombstones(self) -> list[Tombstone]:
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT item_id, deleted_at FROM memory_tombstones ORDER BY item_id"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            Tombstone(item_id=row[0], deleted_at=datetime.fromisoformat(row[1])) for row in rows
        ]

    async def _exists(self, conn: aiosqlite.Connection, table: str, item_id: str) -> bool:
        # ``table`` is one of this module's fixed table names, never input.
        cursor = await conn.execute(f"SELECT 1 FROM {table} WHERE item_id = ?", (item_id,))  # nosec B608
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

    async def import_records(
        self, records: Sequence[MemoryRecord], *, source: ImportSource
    ) -> ImportReport:
        conn = await self._ensure_conn()
        report = ImportReport(received=len(records))
        for record in records:
            cursor = await conn.execute(
                "SELECT text, metadata FROM memories WHERE id = ?", (record.item_id,)
            )
            row = await cursor.fetchone()
            await cursor.close()
            existing: tuple[str, dict[str, Any]] | None = (
                None if row is None else (row[0], json.loads(row[1]) if row[1] else {})
            )
            outcome = classify_import(
                record,
                existing=existing,
                tombstoned=await self._exists(conn, "memory_tombstones", record.item_id),
                rejected=await self._exists(conn, "memory_rejected", record.item_id),
            )
            if outcome == "unchanged":
                report.unchanged += 1
                continue
            if outcome != "imported":
                report.refused[record.item_id] = outcome
                continue
            created = _UNKNOWN_TIME if record.created_at is None else record.created_at.isoformat()
            await conn.execute(
                "INSERT INTO memories (id, text, embedding, metadata, created_at, status, "
                "provenance) VALUES (?, ?, ?, ?, ?, 'quarantined', ?)",
                (
                    record.item_id,
                    record.text,
                    json.dumps(self._embed(record.text)),
                    json.dumps(record.metadata),
                    created,
                    source.for_record(record.item_id).model_dump_json(),
                ),
            )
            report.imported += 1
        await conn.commit()
        return report

    async def delete_record(self, item_id: str, *, reason: str) -> Tombstone:
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT deleted_at FROM memory_tombstones WHERE item_id = ?", (item_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is not None:
            return Tombstone(item_id=item_id, deleted_at=datetime.fromisoformat(row[0]))
        tombstone = Tombstone(item_id=item_id, deleted_at=utcnow())
        await conn.execute("DELETE FROM memories WHERE id = ?", (item_id,))
        await conn.execute(
            "INSERT INTO memory_tombstones (item_id, deleted_at, reason) VALUES (?, ?, ?)",
            (item_id, tombstone.deleted_at.isoformat(), reason),
        )
        await conn.commit()
        return tombstone

    async def review_records(
        self, item_ids: Sequence[str], *, decision: Literal["activate", "reject"], reviewer: str
    ) -> ReviewReport:
        conn = await self._ensure_conn()
        report = ReviewReport()
        for item_id in item_ids:
            cursor = await conn.execute("SELECT status FROM memories WHERE id = ?", (item_id,))
            row = await cursor.fetchone()
            await cursor.close()
            if row is None or row[0] != "quarantined":
                report.not_quarantined.append(item_id)
            elif decision == "activate":
                await conn.execute("UPDATE memories SET status = 'active' WHERE id = ?", (item_id,))
                report.activated.append(item_id)
            else:
                await conn.execute("DELETE FROM memories WHERE id = ?", (item_id,))
                await conn.execute(
                    "INSERT OR IGNORE INTO memory_rejected (item_id, rejected_at, reviewer) "
                    "VALUES (?, ?, ?)",
                    (item_id, utcnow().isoformat(), reviewer),
                )
                report.rejected.append(item_id)
        await conn.commit()
        return report
