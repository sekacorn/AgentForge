"""Every vector-comparing memory backend refuses a dimension mismatch.

Comparing vectors of different lengths gives meaningless scores. Before this
fix ``SQLiteMemoryStore.search`` paired them with ``zip(..., strict=False)`` and
returned wrong rankings without an error. Fully offline: SQLite runs on a temp
file, and asyncpg and pgvector are replaced by fakes.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge.exceptions import MemoryBackendError, MemoryDimensionError
from forge.memory.pgvector import PGVectorMemoryStore
from forge.memory.vector import InMemoryVectorStore

PLANTED = "dim-planted-3a7e-do-not-log"


# --- SQLite ------------------------------------------------------------------------------

aiosqlite = pytest.importorskip("aiosqlite")

from forge.memory.sqlite import SQLiteMemoryStore  # noqa: E402


async def test_sqlite_reopened_at_another_dimension_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "m.db"
    async with SQLiteMemoryStore(path, embedding_dim=64) as store:
        await store.add(f"a remembered fact {PLANTED}")

    with pytest.raises(MemoryDimensionError) as raised:
        async with SQLiteMemoryStore(path, embedding_dim=512) as store:
            await store.search("fact")

    message = str(raised.value)
    assert "64-dimension" in message and "512" in message
    assert PLANTED not in message
    assert isinstance(raised.value, MemoryBackendError)


async def test_sqlite_refusal_happens_before_a_second_dimension_is_written(tmp_path: Path) -> None:
    path = tmp_path / "m.db"
    async with SQLiteMemoryStore(path, embedding_dim=64) as store:
        await store.add("first")
    with pytest.raises(MemoryDimensionError):
        await SQLiteMemoryStore(path, embedding_dim=512).add("second")
    with sqlite3.connect(path) as conn:
        lengths = {
            len(json.loads(row[0])) for row in conn.execute("SELECT embedding FROM memories")
        }
    assert lengths == {64}


async def test_sqlite_search_refuses_a_table_holding_two_dimensions(tmp_path: Path) -> None:
    # Earlier versions could write a second dimension into the same table.
    path = tmp_path / "m.db"
    async with SQLiteMemoryStore(path, embedding_dim=64) as store:
        await store.add("written at sixty-four")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO memories (id, text, embedding, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            ("mem_old", PLANTED, json.dumps([0.5] * 32), None, "2026-01-01T00:00:00+00:00"),
        )

    async with SQLiteMemoryStore(path, embedding_dim=64) as store:
        with pytest.raises(MemoryDimensionError) as raised:
            await store.search("sixty")
    assert "32-dimension" in str(raised.value)
    assert PLANTED not in str(raised.value)


async def test_sqlite_matching_dimensions_still_search(tmp_path: Path) -> None:
    path = tmp_path / "m.db"
    async with SQLiteMemoryStore(path, embedding_dim=128) as store:
        await store.add("forge routes tasks to cheap models")
    async with SQLiteMemoryStore(path, embedding_dim=128) as store:
        hits = await store.search("routes tasks")
    assert hits and hits[0].text.startswith("forge routes")


async def test_sqlite_empty_store_accepts_any_dimension(tmp_path: Path) -> None:
    async with SQLiteMemoryStore(tmp_path / "m.db", embedding_dim=16) as store:
        assert await store.search("anything") == []


# --- pgvector (faked asyncpg and pgvector) ------------------------------------------------


def _fake_pgvector(monkeypatch: pytest.MonkeyPatch, declared: str | None) -> MagicMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=declared)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    pool.close = AsyncMock()

    asyncpg = types.ModuleType("asyncpg")
    asyncpg.create_pool = AsyncMock(return_value=pool)  # type: ignore[attr-defined]
    pgvector = types.ModuleType("pgvector")
    pgvector_asyncpg = types.ModuleType("pgvector.asyncpg")
    pgvector_asyncpg.register_vector = AsyncMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg)
    monkeypatch.setitem(sys.modules, "pgvector", pgvector)
    monkeypatch.setitem(sys.modules, "pgvector.asyncpg", pgvector_asyncpg)
    return pool


async def test_pgvector_table_at_another_dimension_is_refused_on_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _fake_pgvector(monkeypatch, "vector(64)")
    store = PGVectorMemoryStore("postgresql://memory-host/db", embedding_dim=128)

    with pytest.raises(MemoryDimensionError) as raised:
        async with store:
            pass

    assert "64-dimension" in str(raised.value) and "128" in str(raised.value)
    assert "memory-host" not in str(raised.value)
    pool.close.assert_awaited_once()
    assert store._pool is None


async def test_pgvector_matching_dimension_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _fake_pgvector(monkeypatch, "vector(128)")
    async with PGVectorMemoryStore("postgresql://memory-host/db", embedding_dim=128) as store:
        assert store._pool is pool


@pytest.mark.parametrize("declared", [None, "vector", "text"])
async def test_pgvector_an_undeclared_dimension_is_not_guessed(
    monkeypatch: pytest.MonkeyPatch, declared: Any
) -> None:
    _fake_pgvector(monkeypatch, declared)
    async with PGVectorMemoryStore("postgresql://memory-host/db", embedding_dim=64) as store:
        assert store._pool is not None


# --- in-memory -------------------------------------------------------------------------


async def test_in_memory_vectors_always_match_the_store() -> None:
    store = InMemoryVectorStore(dim=8)
    await store.add("alpha beta")
    assert all(len(vector) == 8 for vector in store._vectors)
    assert (await store.search("alpha"))[0].text == "alpha beta"


async def test_in_memory_comparison_is_strict() -> None:
    # Not reachable through the public API; proves a mismatch can never be silent.
    store = InMemoryVectorStore(dim=8)
    await store.add("alpha")
    store._vectors[0] = [1.0] * 4
    with pytest.raises(ValueError):
        await store.search("alpha")
