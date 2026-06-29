"""Tests for the SQLite-backed durable memory store.

Hermetic: the database lives under pytest's ``tmp_path`` so nothing touches the
project directory, and embeddings are the same deterministic hashing vectors as
the in-memory store (no embedding API). The whole module is skipped when the
optional ``aiosqlite`` dependency is not installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiosqlite")

from forge.memory.sqlite import SQLiteMemoryStore  # noqa: E402


async def test_add_returns_string_id(tmp_path) -> None:
    async with SQLiteMemoryStore(tmp_path / "m.db") as store:
        item_id = await store.add("a fact to remember")
    assert isinstance(item_id, str)
    assert item_id


async def test_search_ranks_by_similarity(tmp_path) -> None:
    async with SQLiteMemoryStore(tmp_path / "m.db") as store:
        await store.add("forge routes tasks to cheap models")  # strong match
        await store.add("forge also keeps an audit log")  # weak match (only "forge")
        hits = await store.search("forge routes tasks", k=5)

    assert len(hits) == 2
    assert hits[0].text == "forge routes tasks to cheap models"
    assert hits[0].score is not None and hits[1].score is not None
    assert hits[0].score >= hits[1].score


async def test_search_empty_store_returns_empty(tmp_path) -> None:
    async with SQLiteMemoryStore(tmp_path / "m.db") as store:
        assert await store.search("anything") == []


async def test_clear_removes_all_entries(tmp_path) -> None:
    async with SQLiteMemoryStore(tmp_path / "m.db") as store:
        await store.add("forge fact one")
        await store.add("forge fact two")
        await store.clear()
        assert await store.search("forge fact") == []


async def test_persistence_across_reopen(tmp_path) -> None:
    db_path = tmp_path / "persist.db"
    async with SQLiteMemoryStore(db_path) as store:
        await store.add("forge persists memory across restarts")

    # A brand-new store reading the same file finds the earlier item.
    async with SQLiteMemoryStore(db_path) as reopened:
        hits = await reopened.search("forge persists memory", k=5)

    assert hits
    assert any("persists memory across restarts" in hit.text for hit in hits)


async def test_k_limits_result_count(tmp_path) -> None:
    async with SQLiteMemoryStore(tmp_path / "m.db") as store:
        for i in range(5):
            await store.add(f"forge fact number {i}")
        hits = await store.search("forge fact", k=2)

    assert len(hits) <= 2


async def test_metadata_round_trips(tmp_path) -> None:
    async with SQLiteMemoryStore(tmp_path / "m.db") as store:
        await store.add("forge fact with metadata", metadata={"source": "doc1", "page": 3})
        hits = await store.search("forge metadata", k=1)

    assert hits
    assert hits[0].metadata == {"source": "doc1", "page": 3}
