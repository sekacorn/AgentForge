"""
Persistent RAG example — memories survive process restarts.

Run this script twice. The second run finds the facts stored by the first.

    pip install "agentforge-oss[sqlite]"
    python examples/rag_persistent.py
"""

import asyncio

from forge.memory.sqlite import SQLiteMemoryStore


async def main() -> None:
    async with SQLiteMemoryStore("demo_memory.db") as store:
        count_before = len(await store.search("forge", k=100))
        if count_before == 0:
            print("First run — storing facts...")
            await store.add("Forge routes cheap tasks to small models to save cost.")
            await store.add("The audit log is hash-chained and tamper-evident.")
            await store.add("Ollama runs local LLMs with zero API cost.")
            print("Stored 3 facts. Run again to retrieve them.")
        else:
            print(f"Second run — found {count_before} stored facts.")
            hits = await store.search("how does Forge reduce cost?", k=2)
            for hit in hits:
                print(f"  [{hit.score:.3f}] {hit.text}")


asyncio.run(main())
