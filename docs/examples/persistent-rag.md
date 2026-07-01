# Persistent RAG

Based on `examples/rag_persistent.py`. Shows SQLite-backed memory that survives
process restarts.

## The idea

The default `InMemoryVectorStore` loses all facts when the process exits. The
`SQLiteMemoryStore` writes facts to a local database file so they persist across
restarts -- no external infrastructure needed.

## Full example

```python
import asyncio
from forge.memory.sqlite import SQLiteMemoryStore

async def main() -> None:
    async with SQLiteMemoryStore("demo_memory.db") as store:
        count_before = len(await store.search("forge", k=100))
        if count_before == 0:
            print("First run -- storing facts...")
            await store.add("Forge routes cheap tasks to small models to save cost.")
            await store.add("The audit log is hash-chained and tamper-evident.")
            await store.add("Ollama runs local LLMs with zero API cost.")
            print("Stored 3 facts. Run again to retrieve them.")
        else:
            print(f"Second run -- found {count_before} stored facts.")
            hits = await store.search("how does Forge reduce cost?", k=2)
            for hit in hits:
                print(f"  [{hit.score:.3f}] {hit.text}")

asyncio.run(main())
```

## Run it

```bash
pip install "agentforge-oss[sqlite]"
python examples/rag_persistent.py
python examples/rag_persistent.py   # run again -- facts are still there
```

## First run output

```
First run -- storing facts...
Stored 3 facts. Run again to retrieve them.
```

## Second run output

```
Second run -- found 3 stored facts.
  [0.567] Forge routes cheap tasks to small models to save cost.
  [0.432] Ollama runs local LLMs with zero API cost.
```

## pgvector alternative

For production-scale RAG with real ANN (approximate nearest neighbor) search,
use the `PGVectorMemoryStore` backed by PostgreSQL + pgvector:

```bash
pip install "agentforge-oss[pgvector]"
python examples/rag_pgvector.py
```

See the [Memory & RAG guide](../guides/memory.md) for details on all three backends.
