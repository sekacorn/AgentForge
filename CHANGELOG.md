# Changelog

All notable changes to Forge are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

## [0.5.0] — 2026-07-01

### Added
- Policy-as-code governance: PolicyRule, PolicySet, PolicyDecision -- define deny/approve/log rules evaluated before every tool call
- Human-in-the-loop approval gates: async approver callbacks pause execution for human sign-off
- Four new event types: POLICY_EVALUATED, POLICY_APPROVED, POLICY_DENIED, POLICY_LOGGED
- New example: examples/policy_governance.py
- PGVectorMemoryStore: durable RAG backend using PostgreSQL + pgvector with IVFFlat ANN indexing (cosine distance), asyncpg connection pool
- FORGE_PGVECTOR_DSN config for PostgreSQL connection string
- New extra: `pip install "agentforge-oss[pgvector]"` (asyncpg + pgvector-python)
- New example: examples/rag_pgvector.py

## [0.4.0] — 2026-06-30

### Added
- Amazon Bedrock provider: Claude 3.5 Haiku/Sonnet, Llama 3.1 70B/405B, Mistral Large via the unified Converse API
- Standard AWS credential chain (env vars, profile, IAM role) — no custom credential handling
- Native streaming via converse_stream()

## [0.3.0] — 2026-06-29

### Added
- Ollama provider: local LLM execution via Ollama REST API (llama3.2:3b, llama3.1:8b, llama3.1:70b, mistral:7b, qwen2.5:7b, deepseek-r1:8b) — zero cost, no API key, air-gap compatible
- Native streaming support in OllamaProvider (newline-delimited JSON stream)
- OLLAMA_BASE_URL configuration with automatic reachability detection
- SQLiteMemoryStore: persistent RAG backend using stdlib sqlite3 + aiosqlite, zero external vector extension required
- FORGE_MEMORY_BACKEND=sqlite config to enable persistence across restarts
- New example: examples/rag_persistent.py
- OpenTelemetry export: traces (forge.run, forge.agent, forge.model_call, forge.tool_call spans) and metrics (runs, duration, tokens, tool calls) via opentelemetry-sdk
- ConsoleSpanExporter by default (zero infra), OTLP endpoint for production
- FORGE_OTEL_ENABLED, FORGE_OTEL_ENDPOINT, FORGE_OTEL_SERVICE_NAME config
- New example: examples/otel_tracing.py

## [0.2.0] — 2026-06-28

### Added
- Parallel worker execution: the supervisor now runs workers concurrently with `asyncio.gather` in bounded batches (`max_workers`). A pre-flight budget cap refuses a batch before spawning if its worst-case spend would exceed the remaining budget, and a failing worker is isolated gracefully (emits `WORKER_FAILED`, returns a partial result) instead of cancelling its peers.
- Streaming token output through the event bus: `TOKEN_STREAM_START`, `TOKEN_CHUNK` (chunk plus running cumulative text), and `TOKEN_STREAM_END` events, enabled via `stream=True` on `Orchestrator.run()` and the `forge run --stream` CLI flag. `ModelProvider.stream()` ships a buffered default so every provider streams out of the box; `EchoProvider` streams natively offline.
- `WORKER_STARTED` and `WORKER_FAILED` lifecycle events (the event bus now exposes 21 event types, up from 16).
- Community health files: issue templates (bug report, feature request, provider request), a pull request template, and this changelog.

### Changed
- README rewritten to reflect the shipped feature set, including a "What's shipped" status table covering parallel execution, streaming, and all three providers.
- Test suite expanded to 47 hermetic tests (from 38); `mypy --strict` and `ruff` remain clean.

## [0.1.0] — 2026-06-28

### Added
- Multi-agent orchestration: a supervisor decomposes a goal into subtasks, delegates to focused worker agents (sequential execution), and synthesizes the result.
- Intelligent model routing: cost_optimized, quality_first, balanced, fixed strategies.
- Anthropic provider: Claude Haiku 4.5, Sonnet 4.6, Opus 4.8, Fable 5.
- OpenAI provider: gpt-4o-mini, gpt-4o, gpt-4.1, o3.
- Offline deterministic Echo provider (zero config, no API key required).
- Tool sandboxing with allowlist/denylist, per-tool timeouts, dangerous-denied-by-default.
- RBAC with roles: admin, operator, developer, viewer.
- Prompt-injection heuristics and input sanitization.
- SHA-256 hash-chained tamper-evident audit log.
- PII redaction: emails, cards, SSNs, IPs, phones.
- Event bus with 16 lifecycle event types.
- Per-run cost reporting (tokens + USD, per model, per agent).
- Conversation memory and in-memory RAG vector store.
- CLI: forge run, forge models, forge audit.
- 38 hermetic tests, mypy strict clean, ruff clean.
- CI matrix across Python 3.11, 3.12, 3.13.
- PyPI release via OIDC Trusted Publisher (pip install agentforge-oss).
