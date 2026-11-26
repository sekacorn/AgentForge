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
print(hits[0].text)  # -> the cost-routing fact
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

dsn = "postgresql://forge@localhost:5432/forge"  # password from PGPASSWORD or .pgpass
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
export FORGE_PGVECTOR_DSN="postgresql://forge@db-host:5432/forge"
```

Keep the password out of the DSN. asyncpg reads it from `PGPASSWORD` or a
`.pgpass` file (libpq's password file format), so the DSN, which can end up in
shell history, process listings and configuration dumps, names only the user,
host, port and database. Provide `PGPASSWORD` from your secret store at runtime
rather than writing it into a file in the repository.

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
    pgvector_dsn="postgresql://forge@localhost:5432/forge",  # password from PGPASSWORD
)
```

---

## Portable memory

The bundled stores (`InMemoryVectorStore`, `SQLiteMemoryStore`,
`PGVectorMemoryStore`) implement `forge.memory.PortableMemory`, so their
contents can leave and arrive intact. A third-party `Memory` that does not is
still usable; it is just not exportable.

```python
from forge.memory import ImportSource, MemoryRecord, PortableMemory

assert isinstance(store, PortableMemory)
page = await store.list_records(limit=1000)  # ordered by item_id; follow page.next_after
report = await store.import_records(records, source=source)
await store.review_records(["imp-1"], decision="activate", reviewer="alice")
await store.delete_record("mem_123", reason="erasure request")
```

**Imported memory is untrusted.** Imported items keep their IDs and arrive
`quarantined`: they are listed and exported with that status, but `search`
never returns them until a reviewer activates them. Rejecting an item removes
it and stops it from being imported again. An item with the same ID and
different content is refused rather than overwritten, and an import carrying
`_forge_*` metadata keys is refused, so a document cannot forge provenance.
Provenance (where the item came from, which export, when, by which importer)
is stored apart from the item's own metadata.

**Deletions stick.** `delete_record` leaves a tombstone holding only the ID and
the time. No import resurrects a tombstoned ID.

**Migrations.** Opening an existing SQLite or pgvector store runs an additive
migration automatically: every statement is idempotent, nothing is dropped or
renamed, and existing items are active. To run it deliberately, or to hand it
to a DBA, use the CLI:

```bash
forge memory migrate              # apply it to the configured store
forge memory migrate --print-sql  # print the statements; opens no connection
```

On pgvector it adds the columns `item_id`, `status` and `provenance`, the unique
index `<table>_item_id_idx` (built `CONCURRENTLY`, so writes continue while it
builds) and the tables `<table>_tombstones` and `<table>_rejected`. Each
`ADD COLUMN` holds an `ACCESS EXCLUSIVE` lock only briefly. If the database user
lacks ALTER or CREATE privileges, opening the store fails with
`MemoryMigrationError` explaining what to run; have the table owner apply the
`--print-sql` output. An index left invalid by an interrupted concurrent build
is reported, never skipped: drop it with `DROP INDEX CONCURRENTLY` and run
`forge memory migrate` again.

### Exporting, importing and reviewing

```bash
forge memory export ./memory-export --export-id exp-2026-12-01   # agent-memory.v1
forge memory import ./memory-export --report ./import-report.json
forge memory quarantine                                          # what arrived, never the text
forge memory review --from-export exp-2026-12-01 --activate --reviewer alice
forge memory review mem_42 --activate --accept-flagged --reviewer alice
```

An export is a directory: `agent-memory.json` with the digest of everything
else, the items in JSONL shards, and the vectors in a linked
`embedding-portability.v1` inventory. The AI Infrastructure Suite reads it as
evidence for the `memory` lock-in vector; with the import report beside it, a
verified export scores 25.

- **Credentials stop the export.** If any item still looks like a secret, the
  export writes nothing and names the item and the kind of secret, never the
  value. There is no way to export the secret itself. To proceed, choose one:
  - `--redact-secrets` replaces each detected credential with
    `[REDACTED:<kind>]`; the document records that secrets were redacted.
  - `--exclude ITEM --exclude-reason "why"` (repeatable) leaves those items out
    of this export, for example a reviewed false positive. The reason is
    required and each exclusion is written to the audit log with the operator
    (`--operator`) and the export digest. The item stays in the store.
  - Delete the item under policy; it then travels only as a tombstone.

  `--redact-pii` applies the PII redactor first.
- **Deleted items stay deleted.** They travel as tombstones (an ID and a
  time), which the import applies before anything else.
- **Imports are quarantined.** Nothing imported is searchable until reviewed.
  `forge memory quarantine` lists each item's source and any prompt-injection
  indicators. Bulk activation skips flagged items; activate one deliberately
  with `--accept-flagged`, or reject it, which also stops it being imported
  again. Decisions are recorded in the audit log.
- **Memory never becomes a system prompt.** Put retrieved memory in front of a
  model with `forge.memory.context.memory_context_message`, which quotes it in a
  user message and labels it untrusted.

What an export **cannot** promise: that the source system really deleted
anything, that earlier exports, backups or caches no longer hold the data, or
that the redaction caught every kind of personal data.

