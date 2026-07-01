# Examples

Runnable end-to-end scripts in the [`examples/`](https://github.com/sekacorn/AgentForge/tree/main/examples) directory. Every example works offline with the Echo provider unless noted otherwise.

| Example | What it demonstrates |
|---|---|
| [Custom tools](custom-tools.md) | The `@tool` decorator, custom functions, and tool registries |
| [Parallel supervisor](parallel-supervisor.md) | Supervisor mode with concurrent workers and cost reporting |
| [Persistent RAG](persistent-rag.md) | SQLite-backed memory that survives process restarts |
| [Policy governance](policy-governance.md) | Deny, approve, and log rules with human-in-the-loop gates |
| [OpenTelemetry](otel-tracing.md) | Console span export and OTLP endpoint configuration |

Additional scripts in the repository:

- **`examples/quickstart.py`** -- a multi-agent run with live event rendering
- **`examples/rag.py`** -- in-memory RAG with retrieval-augmented generation
- **`examples/enterprise_governance.py`** -- RBAC, budgets, audit verification, PII redaction
- **`examples/rag_pgvector.py`** -- pgvector-backed durable RAG with PostgreSQL
