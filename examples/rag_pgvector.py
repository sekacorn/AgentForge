"""Persistent RAG with PostgreSQL + pgvector.

Demonstrates inserting facts into a pgvector-backed memory store and querying
them with semantic similarity search. Requires a running PostgreSQL instance
with the ``vector`` extension installed.

Set ``FORGE_PGVECTOR_DSN`` or pass the DSN directly::

    export FORGE_PGVECTOR_DSN="postgresql://forge:forge@localhost:5432/forge"
    python examples/rag_pgvector.py

Install the optional dependency::

    pip install "agentforge-oss[pgvector]"
"""

from __future__ import annotations

import asyncio
import os

from forge import PGVectorMemoryStore


async def main() -> None:
    dsn = os.environ.get("FORGE_PGVECTOR_DSN", "postgresql://forge:forge@localhost:5432/forge")

    async with PGVectorMemoryStore(dsn) as store:
        await store.clear()

        await store.add("Forge is an open-source multi-agent orchestration platform.")
        await store.add("pgvector enables real semantic vector search in PostgreSQL.")
        await store.add("IVFFlat indexes give approximate nearest neighbor performance at scale.")
        await store.add("The Echo provider works offline with zero configuration.")

        print("Stored 4 facts. Searching for 'vector search in postgres'...\n")

        results = await store.search("vector search in postgres", k=3)
        for i, item in enumerate(results, 1):
            print(f"  {i}. (score={item.score:.4f}) {item.text}")

        print()


if __name__ == "__main__":
    asyncio.run(main())
