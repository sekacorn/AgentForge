# Memory & RAG

Forge provides pluggable memory backends behind a single `Memory` interface.
All backends support the same three operations: `add`, `search`, and `clear`.

Three backends ship in the box:

| Backend | Persistence | Dependencies | Best for |
|---|---|---|---|
| `InMemoryVectorStore` | None (process lifetime) | None | Development, testing, short-lived tasks |
| `SQLiteMemoryStore` | File on disk | `aiosqlite` | Single-node apps, SQLite-friendly deploys |
| `PGVectorMemoryStore` | PostgreSQL | `asyncpg`, `pgvector` | Production, multi-node, real ANN search |

---

## InMemoryVectorStore

The default. Zero dependencies, zero configuration. Facts are lost when the
process exits.

```python
from forge import InMemoryVectorStore

store = InMemoryVectorStore()
await store.add("Forge routes cheap tasks to small models to save cost.")
await store.add("The audit log is hash-chained and tamper-evident.")

hits = await store.search("how does Forge keep costs down?", k=1)
print(hits[0].text)   # -> the cost-routing fact
```

### How it works

Text is tokenized, hashed into a fixed-dimension vector using blake2b, and
compared by cosine similarity. No external model or API is needed -- the
embedding is deterministic and runs offline.

---

## SQLiteMemoryStore

Persistent RAG backed by SQLite. Facts survive process restarts. Uses the same
hash-based embedding as the in-memory store, with cosine similarity computed in
Python.

### Install

```bash
pip install "agentforge-oss[sqlite]"
```

### Usage

```python
from forge.memory.sqlite import SQLiteMemoryStore

async with SQLiteMemoryStore("my_memory.db") as store:
    await store.add("Forge routes cheap tasks to small models to save cost.")
    hits = await store.search("cost optimization", k=2)
    for hit in hits:
        print(f"[{hit.score:.3f}] {hit.text}")
```

### Surviving a restart

Run the script twice -- the second run retrieves facts stored by the first:

```python
async with SQLiteMemoryStore("demo_memory.db") as store:
    count = len(await store.search("forge", k=100))
    if count == 0:
        print("First run -- storing facts...")
        await store.add("Forge routes cheap tasks to small models.")
        await store.add("The audit log is hash-chained.")
        print("Stored 2 facts. Run again to retrieve them.")
    else:
        print(f"Second run -- found {count} stored facts.")
        hits = await store.search("how does Forge reduce cost?", k=1)
        print(hits[0].text)
```

### Configure via environment

```bash
export FORGE_MEMORY_BACKEND=sqlite
export FORGE_MEMORY_PATH=forge_memory.db
```

---

## PGVectorMemoryStore

Production-grade RAG backed by PostgreSQL with the `pgvector` extension.
Unlike the SQLite backend (which computes cosine similarity in Python), this
backend delegates vector search to PostgreSQL's IVFFlat index for real
approximate nearest neighbor (ANN) performance at scale.

### Install

```bash
pip install "agentforge-oss[pgvector]"
```

Your PostgreSQL instance needs the `vector` extension:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### Usage

```python
from forge import PGVectorMemoryStore

dsn = "postgresql://forge:forge@localhost:5432/forge"
async with PGVectorMemoryStore(dsn) as store:
    await store.add("Forge is an open-source multi-agent platform.")
    await store.add("pgvector enables real semantic vector search.")

    hits = await store.search("vector search", k=2)
    for hit in hits:
        print(f"[{hit.score:.4f}] {hit.text}")
```

### Schema

The store auto-creates the table and index on first connection:

| Column | Type | Description |
|---|---|---|
| `id` | `UUID` | Auto-generated primary key |
| `text` | `TEXT` | The stored document text |
| `embedding` | `vector(64)` | Hash-based embedding vector |
| `metadata` | `JSONB` | Optional structured metadata |
| `created_at` | `TIMESTAMPTZ` | Insertion timestamp |

An IVFFlat index with `lists=100` is created automatically for cosine distance.

### Configure via environment

```bash
export FORGE_MEMORY_BACKEND=pgvector
export FORGE_PGVECTOR_DSN="postgresql://user:pass@host:5432/db"
```

### Custom table and dimensions

```python
store = PGVectorMemoryStore(
    dsn,
    table="custom_memories",
    embedding_dim=128,
)
```

---

## The Memory interface

All backends implement the `Memory` ABC:

```python
from forge.memory.base import Memory, MemoryItem

class Memory(ABC):
    async def add(self, text: str, *, metadata: dict | None = None) -> str: ...
    async def search(self, query: str, *, k: int = 5) -> list[MemoryItem]: ...
    async def clear(self) -> None: ...
```

`MemoryItem` has four fields: `id`, `text`, `metadata`, and `score`.

To add a new backend (Redis, Pinecone, Weaviate, ...), implement these three
methods and pass the store to the orchestrator.

---

## Wiring memory into the orchestrator

The `build_memory()` factory reads `config.memory_backend` and returns the
appropriate store. The orchestrator calls this automatically:

```python
from forge import ForgeConfig, Orchestrator

config = ForgeConfig(memory_backend="sqlite", memory_path="my_rag.db")
async with Orchestrator(config) as forge:
    # The orchestrator uses SQLiteMemoryStore internally
    ...
```

For pgvector:

```python
config = ForgeConfig(
    memory_backend="pgvector",
    pgvector_dsn="postgresql://forge:forge@localhost:5432/forge",
)
```
