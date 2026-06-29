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

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING

from forge.memory.base import Memory, MemoryItem
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


class SQLiteMemoryStore(Memory):
    """A durable cosine-similarity store backed by a SQLite database file."""

    def __init__(self, path: str | Path = "forge_memory.db", *, embedding_dim: int = 64) -> None:
        self._path = str(path)
        self._dim = embedding_dim
        self._conn: aiosqlite.Connection | None = None

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
        """Open the connection (and create the schema) on first use."""
        if self._conn is not None:
            return self._conn
        try:
            import aiosqlite
        except ImportError as exc:
            raise ImportError(_AIOSQLITE_MISSING) from exc
        conn = await aiosqlite.connect(self._path)
        await conn.execute(_CREATE_TABLE_SQL)
        await conn.commit()
        self._conn = conn
        return conn

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
        cursor = await conn.execute("SELECT id, text, embedding, metadata FROM memories")
        rows = await cursor.fetchall()
        await cursor.close()

        scored: list[MemoryItem] = []
        for row in rows:
            vector: list[float] = json.loads(row[2])
            score = sum(a * b for a, b in zip(query_vector, vector, strict=False))
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
