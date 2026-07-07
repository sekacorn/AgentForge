"""Tests for PGVectorMemoryStore (fully mocked -- no real PostgreSQL needed)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge.memory.pgvector import PGVectorMemoryStore, _stable_bucket, _tokenize

# ---------------------------------------------------------------------------
# Pure helpers (no mocking needed)
# ---------------------------------------------------------------------------


def test_tokenize_basic() -> None:
    assert _tokenize("Hello World 123") == ["hello", "world", "123"]


def test_tokenize_strips_punctuation() -> None:
    assert _tokenize("it's a test!") == ["it", "s", "a", "test"]


def test_stable_bucket_deterministic() -> None:
    a = _stable_bucket("hello", 64)
    b = _stable_bucket("hello", 64)
    assert a == b


def test_stable_bucket_range() -> None:
    for word in ("alpha", "beta", "gamma"):
        bucket = _stable_bucket(word, 64)
        assert 0 <= bucket < 64


def test_embed_returns_correct_dim() -> None:
    store = PGVectorMemoryStore("postgresql://unused")
    vec = store._embed("hello world")
    assert len(vec) == 64


def test_embed_l2_normalized() -> None:
    import math

    store = PGVectorMemoryStore("postgresql://unused")
    vec = store._embed("some text for testing normalization")
    norm = math.sqrt(sum(v * v for v in vec))
    assert abs(norm - 1.0) < 1e-9


def test_embed_empty_text_returns_zeros() -> None:
    store = PGVectorMemoryStore("postgresql://unused")
    vec = store._embed("!@#$%")
    assert all(v == 0.0 for v in vec)


# ---------------------------------------------------------------------------
# Async methods (mocked asyncpg)
# ---------------------------------------------------------------------------


def _make_mock_pool() -> tuple[MagicMock, AsyncMock]:
    """Build a mock asyncpg pool with acquire() async context manager.

    Returns (pool, conn) so tests can configure the conn mock directly.
    asyncpg's pool.acquire() returns a synchronous context-manager object
    whose __aenter__/__aexit__ are async; we replicate that here.
    """
    conn = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    pool.close = AsyncMock()
    return pool, conn


@pytest.mark.asyncio
async def test_add_inserts_and_returns_uuid() -> None:
    pool, conn = _make_mock_pool()
    row_id = str(uuid.uuid4())
    conn.fetchrow = AsyncMock(return_value={"id": row_id})

    store = PGVectorMemoryStore("postgresql://test")
    store._pool = pool

    result = await store.add("test fact", metadata={"source": "unit_test"})
    assert result == row_id
    conn.fetchrow.assert_called_once()
    call_args = conn.fetchrow.call_args
    assert "INSERT INTO" in call_args[0][0]


@pytest.mark.asyncio
async def test_add_without_metadata() -> None:
    pool, conn = _make_mock_pool()
    row_id = str(uuid.uuid4())
    conn.fetchrow = AsyncMock(return_value={"id": row_id})

    store = PGVectorMemoryStore("postgresql://test")
    store._pool = pool

    result = await store.add("bare fact")
    assert result == row_id
    call_args = conn.fetchrow.call_args
    assert call_args[0][3] is None


@pytest.mark.asyncio
async def test_search_returns_memory_items() -> None:
    pool, conn = _make_mock_pool()

    fake_rows = [
        {
            "id": uuid.uuid4(),
            "text": "fact one",
            "metadata": json.dumps({"k": "v"}),
            "similarity": 0.95,
        },
        {"id": uuid.uuid4(), "text": "fact two", "metadata": None, "similarity": 0.80},
    ]
    conn.fetch = AsyncMock(return_value=fake_rows)

    store = PGVectorMemoryStore("postgresql://test")
    store._pool = pool

    items = await store.search("query text", k=2)
    assert len(items) == 2
    assert items[0].text == "fact one"
    assert items[0].score == 0.95
    assert items[0].metadata == {"k": "v"}
    assert items[1].text == "fact two"
    assert items[1].metadata == {}


@pytest.mark.asyncio
async def test_clear_deletes_all_rows() -> None:
    pool, conn = _make_mock_pool()

    store = PGVectorMemoryStore("postgresql://test")
    store._pool = pool

    await store.clear()
    conn.execute.assert_called_once()
    assert "DELETE FROM" in conn.execute.call_args[0][0]


@pytest.mark.asyncio
async def test_aclose_closes_pool() -> None:
    pool, _conn = _make_mock_pool()

    store = PGVectorMemoryStore("postgresql://test")
    store._pool = pool

    await store.aclose()
    pool.close.assert_called_once()
    assert store._pool is None


@pytest.mark.asyncio
async def test_aclose_idempotent() -> None:
    store = PGVectorMemoryStore("postgresql://test")
    store._pool = None
    await store.aclose()


@pytest.mark.asyncio
async def test_custom_table_and_dim() -> None:
    store = PGVectorMemoryStore("postgresql://test", table="custom_table", embedding_dim=128)
    assert store._table == "custom_table"
    assert store._dim == 128
    vec = store._embed("hello")
    assert len(vec) == 128


def test_invalid_table_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="PostgreSQL identifiers"):
        PGVectorMemoryStore("postgresql://test", table="bad; DROP TABLE memories")


def test_import_error_message() -> None:
    from forge.memory.pgvector import _ASYNCPG_MISSING

    assert "asyncpg" in _ASYNCPG_MISSING
    assert "pgvector" in _ASYNCPG_MISSING
    assert "pip install" in _ASYNCPG_MISSING
