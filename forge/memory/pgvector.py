"""A PostgreSQL + pgvector-backed durable memory store for RAG.

Unlike the SQLite backend which computes cosine similarity in Python, this
backend delegates vector search to PostgreSQL's pgvector extension with an
IVFFlat index, giving real ANN (approximate nearest neighbor) performance at
scale.

``asyncpg`` and ``pgvector-python`` are *optional* dependencies imported lazily
so the core library works without them. Install with::

    pip install "agentforge-oss[pgvector]"

Use it as an async context manager::

    async with PGVectorMemoryStore("postgresql://forge@db-host:5432/forge") as store:
        await store.add("a fact to remember")
        hits = await store.search("a query")

Keep the password out of the DSN: asyncpg reads it from ``PGPASSWORD`` or a
``.pgpass`` file, so the DSN names only the user, host, port and database.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from typing import Any, Literal

from forge.exceptions import MemoryDimensionError, MemoryMigrationError
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
from forge.types import utcnow

_ASYNCPG_MISSING = (
    "pgvector memory backend requires asyncpg and pgvector: pip install 'agentforge-oss[pgvector]'"
)

_TOKEN = re.compile(r"[a-z0-9]+")
_SQL_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_VECTOR_TYPE = re.compile(r"vector\((\d+)\)\Z")
#: A row's portable ID. Native rows keep their UUID as text, so the IDs ``add``
#: and ``search`` return never change; an imported row keeps its original ID in
#: the nullable ``item_id`` column added for portability.
_PORTABLE_ID = "COALESCE(item_id, id::text)"


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
        self._item_index_sql = _quote_identifier(f"{table}_item_id_idx")
        self._tombstones_sql = _quote_identifier(f"{table}_tombstones")
        self._rejected_sql = _quote_identifier(f"{table}_rejected")
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
            # CREATE TABLE IF NOT EXISTS keeps an existing table's dimension;
            # refuse a mismatch here instead of failing on every query later.
            declared = await conn.fetchval(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = to_regclass($1) AND attname = 'embedding'",
                self._table_sql,
            )
            match = _VECTOR_TYPE.fullmatch(declared) if isinstance(declared, str) else None
            stored = int(match.group(1)) if match is not None else None
            failure: MemoryMigrationError | None = None
            # Refuse a dimension mismatch before touching the schema at all.
            if stored is None or stored == self._dim:
                try:
                    await self._migrate(conn)
                except MemoryMigrationError as exc:
                    failure = exc
        if stored is not None and stored != self._dim:
            await self.aclose()
            raise MemoryDimensionError(dimension_mismatch_message("pgvector", stored, self._dim))
        if failure is not None:
            # Close the pool first; the error carries no database text.
            await self.aclose()
            raise failure
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    def migration_sql(self) -> list[str]:
        """The additive statements that portability adds to an existing table.

        Every statement is idempotent (``IF NOT EXISTS``) and nothing is dropped
        or renamed. ``forge memory migrate --print-sql`` prints exactly these, so a
        DBA can apply them by hand. Lock behaviour: ``ADD COLUMN`` with a NULL or
        constant default is a catalog-only change that holds an ACCESS EXCLUSIVE
        lock only briefly; the index is built ``CONCURRENTLY``, so writes continue
        while it builds.
        """
        # Identifiers are validated and quoted; everything else is fixed text.
        return [
            f"ALTER TABLE {self._table_sql} ADD COLUMN IF NOT EXISTS item_id TEXT",
            f"ALTER TABLE {self._table_sql} "
            f"ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'",
            f"ALTER TABLE {self._table_sql} ADD COLUMN IF NOT EXISTS provenance JSONB",
            f"CREATE TABLE IF NOT EXISTS {self._tombstones_sql} ("
            f"item_id TEXT PRIMARY KEY, deleted_at TIMESTAMPTZ NOT NULL, reason TEXT NOT NULL)",
            f"CREATE TABLE IF NOT EXISTS {self._rejected_sql} ("
            f"item_id TEXT PRIMARY KEY, rejected_at TIMESTAMPTZ NOT NULL, reviewer TEXT NOT NULL)",
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {self._item_index_sql} "
            f"ON {self._table_sql} (({_PORTABLE_ID}))",
        ]

    def _migration_failed(self, sqlstate: object) -> MemoryMigrationError:
        if sqlstate == "42501":  # insufficient_privilege
            return MemoryMigrationError(
                f"The database user may not change table {self._table} (it lacks ALTER or "
                "CREATE privileges). Ask the table owner or a DBA to run "
                "`forge memory migrate --print-sql` and apply the statements it prints, "
                "then open the store again."
            )
        return MemoryMigrationError(
            f"Migrating memory table {self._table} failed (SQLSTATE {sqlstate}). Run "
            "`forge memory migrate --print-sql` to see the statements, which are idempotent "
            "and additive, and apply them as the table owner."
        )

    async def _migrate(self, conn: Any) -> None:
        """Additive schema for portability. Existing rows stay active and native."""
        # A concurrent index build that was interrupted leaves an INVALID index,
        # which IF NOT EXISTS would then skip forever without enforcing anything.
        valid = await conn.fetchval(
            "SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass($1)",
            self._item_index_sql,
        )
        if valid is False:
            raise MemoryMigrationError(
                f"Index {self._table}_item_id_idx exists but is invalid (an interrupted "
                f"concurrent build). Run DROP INDEX CONCURRENTLY {self._item_index_sql}; "
                "then `forge memory migrate`."
            )
        for statement in self.migration_sql():
            try:
                # Outside any transaction, as CREATE INDEX CONCURRENTLY requires.
                await conn.execute(statement)  # nosec B608
            except Exception as exc:
                sqlstate = getattr(exc, "sqlstate", None)
                if sqlstate is None:
                    raise
                raise self._migration_failed(sqlstate) from None

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
                f"SELECT {_PORTABLE_ID} AS id, text, metadata, "  # nosec B608
                f"1 - (embedding <=> $1::vector) AS similarity "
                f"FROM {self._table_sql} "
                f"WHERE status = 'active' "
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

    # --- PortableMemory ------------------------------------------------------------

    @property
    def embedding_scheme(self) -> EmbeddingScheme:
        return EmbeddingScheme(
            scheme_id=HASHED_BOW_SCHEME, revision=HASHED_BOW_REVISION, dimensions=self._dim
        )

    async def list_records(self, *, after: str | None = None, limit: int = 1000) -> RecordPage:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_PORTABLE_ID} AS id, text, metadata, created_at, status, "  # nosec B608
                f"provenance FROM {self._table_sql} "
                f'WHERE ($1::text IS NULL OR {_PORTABLE_ID} COLLATE "C" > $1) '
                f'ORDER BY {_PORTABLE_ID} COLLATE "C" LIMIT $2',
                after,
                limit + 1,
            )
        records = [
            MemoryRecord(
                item_id=str(row["id"]),
                text=row["text"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                created_at=row["created_at"],
                status=row["status"],
                provenance=None
                if row["provenance"] is None
                else ImportProvenance.model_validate_json(row["provenance"]),
            )
            for row in rows[:limit]
        ]
        more = len(rows) > limit
        return RecordPage(records=records, next_after=records[-1].item_id if more else None)

    async def list_tombstones(self) -> list[Tombstone]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT item_id, deleted_at FROM {self._tombstones_sql} "  # nosec B608
                f'ORDER BY item_id COLLATE "C"'
            )
        return [Tombstone(item_id=row["item_id"], deleted_at=row["deleted_at"]) for row in rows]

    async def import_records(
        self, records: Sequence[MemoryRecord], *, source: ImportSource
    ) -> ImportReport:
        report = ImportReport(received=len(records))
        async with self._pool.acquire() as conn:
            for record in records:
                existing_row = await conn.fetchrow(
                    f"SELECT text, metadata FROM {self._table_sql} "  # nosec B608
                    f"WHERE {_PORTABLE_ID} = $1",
                    record.item_id,
                )
                existing: tuple[str, dict[str, Any]] | None = None
                if existing_row is not None:
                    raw = existing_row["metadata"]
                    existing = (existing_row["text"], json.loads(raw) if raw else {})
                tombstoned = await conn.fetchval(
                    f"SELECT 1 FROM {self._tombstones_sql} WHERE item_id = $1",  # nosec B608
                    record.item_id,
                )
                rejected = await conn.fetchval(
                    f"SELECT 1 FROM {self._rejected_sql} WHERE item_id = $1",  # nosec B608
                    record.item_id,
                )
                outcome = classify_import(
                    record,
                    existing=existing,
                    tombstoned=tombstoned is not None,
                    rejected=rejected is not None,
                )
                if outcome == "unchanged":
                    report.unchanged += 1
                    continue
                if outcome != "imported":
                    report.refused[record.item_id] = outcome
                    continue
                await conn.execute(
                    f"INSERT INTO {self._table_sql} (item_id, text, embedding, metadata, "  # nosec B608
                    f"created_at, status, provenance) "
                    f"VALUES ($1, $2, $3, $4::jsonb, $5, 'quarantined', $6::jsonb)",
                    record.item_id,
                    record.text,
                    self._embed(record.text),
                    json.dumps(record.metadata),
                    record.created_at,
                    source.for_record(record.item_id).model_dump_json(),
                )
                report.imported += 1
        return report

    async def delete_record(self, item_id: str, *, reason: str) -> Tombstone:
        async with self._pool.acquire() as conn:
            deleted_at = await conn.fetchval(
                f"SELECT deleted_at FROM {self._tombstones_sql} WHERE item_id = $1",  # nosec B608
                item_id,
            )
            if deleted_at is not None:
                return Tombstone(item_id=item_id, deleted_at=deleted_at)
            tombstone = Tombstone(item_id=item_id, deleted_at=utcnow())
            await conn.execute(
                f"DELETE FROM {self._table_sql} WHERE {_PORTABLE_ID} = $1",  # nosec B608
                item_id,
            )
            await conn.execute(
                f"INSERT INTO {self._tombstones_sql} (item_id, deleted_at, reason) "  # nosec B608
                f"VALUES ($1, $2, $3)",
                item_id,
                tombstone.deleted_at,
                reason,
            )
        return tombstone

    async def review_records(
        self, item_ids: Sequence[str], *, decision: Literal["activate", "reject"], reviewer: str
    ) -> ReviewReport:
        report = ReviewReport()
        async with self._pool.acquire() as conn:
            for item_id in item_ids:
                status = await conn.fetchval(
                    f"SELECT status FROM {self._table_sql} WHERE {_PORTABLE_ID} = $1",  # nosec B608
                    item_id,
                )
                if status != "quarantined":
                    report.not_quarantined.append(item_id)
                elif decision == "activate":
                    await conn.execute(
                        f"UPDATE {self._table_sql} SET status = 'active' "  # nosec B608
                        f"WHERE {_PORTABLE_ID} = $1",
                        item_id,
                    )
                    report.activated.append(item_id)
                else:
                    await conn.execute(
                        f"DELETE FROM {self._table_sql} WHERE {_PORTABLE_ID} = $1",  # nosec B608
                        item_id,
                    )
                    await conn.execute(
                        f"INSERT INTO {self._rejected_sql} (item_id, rejected_at, reviewer) "  # nosec B608
                        f"VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                        item_id,
                        utcnow(),
                        reviewer,
                    )
                    report.rejected.append(item_id)
        return report
