"""Concurrency regression test for SQLiteMemoryStore lazy connection init.

Concurrent first use must open exactly one connection (guarded by a lock) rather
than racing to create duplicates. Skips cleanly without the optional aiosqlite dep.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("aiosqlite")

from forge.memory.sqlite import SQLiteMemoryStore  # noqa: E402


async def test_concurrent_first_use_is_safe(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "concurrent.db")
    try:
        # All three adds race on the very first (lazy) connection.
        await asyncio.gather(
            store.add("forge fact one"),
            store.add("forge fact two"),
            store.add("forge fact three"),
        )
        hits = await store.search("forge fact", k=10)
        assert len(hits) == 3
    finally:
        await store.aclose()
