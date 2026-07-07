"""A PostgreSQL + pgvector-backed durable memory store for RAG.

Unlike the SQLite backend which computes cosine similarity in Python, this
backend delegates vector search to PostgreSQL's pgvector extension with an
IVFFlat index, giving real ANN (approximate nearest neighbor) performance at
scale.

``asyncpg`` and ``pgvector-python`` are *optional* dependencies imported lazily
so the core library works without them. Install with::

    pip install "agentforge-oss[pgvector]"

Use it as an async context manager::

    async with PGVectorMemoryStore("postgresql://user:pass@host/db") as store:
        await store.add("a fact to remember")
        hits = await store.search("a query")
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from forge.memory.base import Memory, MemoryItem

_ASYNCPG_MISSING = (
    "pgvector memory backend requires asyncpg and pgvector: pip install 'agentforge-oss[pgvector]'"
)

_TOKEN = re.compile(r"[a-z0-9]+")
_SQL_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _stable_bucket(token: str, dim: int) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dim


def _quote_identifier(identifier: str) -> str:
    if _SQL_IDENTIFIER.fullmatch(identifier) is None:
        raise ValueError(
            "PostgreSQL identifiers must start with a letter or underscore and "
            "contain only letters, numbers, and underscores."
        )
    return f'"{identifier}"'


class PGVectorMemoryStore(Memory):
    """A durable cosine-similarity store backed by PostgreSQL + pgvector."""

    def __init__(self, dsn: str, *, table: str = "forge_memories", embedding_dim: int = 64) -> None:
        self._dsn = dsn
        self._table = table
        self._table_sql = _quote_identifier(table)
        self._index_sql = _quote_identifier(f"{table}_embedding_idx")
        self._dim = embedding_dim
        self._pool: Any = None

    def _embed(self, text: str) -> list[float]:
        """Hash tokens into a fixed-dimension, L2-normalized term-frequency vector."""
        vector = [0.0] * self._dim
        for token in _tokenize(text):
            vector[_stable_bucket(token, self._dim)] += 1.0
        norm = math.sqrt(sum(component * component for component in vector))
        if norm > 0:
            vector = [component / norm for component in vector]
        return vector

    async def __aenter__(self) -> PGVectorMemoryStore:
        try:
            import asyncpg
        except ImportError as exc:
            raise ImportError(_ASYNCPG_MISSING) from exc
        try:
            from pgvector.asyncpg import register_vector
        except ImportError as exc:
            raise ImportError(_ASYNCPG_MISSING) from exc

        self._pool = await asyncpg.create_pool(dsn=self._dsn)
        async with self._pool.acquire() as conn:
            await register_vector(conn)
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            # SQL identifiers are validated and quoted before interpolation.
            await conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._table_sql} ("  # nosec B608
                f"  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
                f"  text TEXT NOT NULL,"
                f"  embedding vector({self._dim}),"
                f"  metadata JSONB,"
                f"  created_at TIMESTAMPTZ DEFAULT now()"
                f")"
            )
            # lists=100 is appropriate for up to ~1M rows; tune for larger datasets
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {self._index_sql} "  # nosec B608
                f"ON {self._table_sql} USING ivfflat (embedding vector_cosine_ops) "
                f"WITH (lists = 100)"
            )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def add(self, text: str, *, metadata: dict[str, object] | None = None) -> str:
        import json

        embedding = self._embed(text)
        meta_json = json.dumps(metadata) if metadata is not None else None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"INSERT INTO {self._table_sql} (text, embedding, metadata) "  # nosec B608
                f"VALUES ($1, $2, $3::jsonb) RETURNING id",
                text,
                embedding,
                meta_json,
            )
        return str(row["id"])

    async def search(self, query: str, *, k: int = 5) -> list[MemoryItem]:
        import json

        query_vector = self._embed(query)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, text, metadata, "  # nosec B608
                f"1 - (embedding <=> $1::vector) AS similarity "
                f"FROM {self._table_sql} "
                f"ORDER BY embedding <=> $1::vector "
                f"LIMIT $2",
                query_vector,
                k,
            )
        items: list[MemoryItem] = []
        for row in rows:
            raw_meta = row["metadata"]
            metadata: dict[str, object] = json.loads(raw_meta) if raw_meta else {}
            items.append(
                MemoryItem(
                    id=str(row["id"]),
                    text=row["text"],
                    metadata=metadata,
                    score=round(float(row["similarity"]), 6),
                )
            )
        return items

    async def clear(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(f"DELETE FROM {self._table_sql}")  # nosec B608

    async def aclose(self) -> None:
        """Close the connection pool. Safe to call more than once."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
