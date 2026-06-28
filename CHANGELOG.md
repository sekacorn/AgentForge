# Changelog

All notable changes to Forge are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [0.1.0] — 2026-06-28

### Added
- Multi-agent orchestration: supervisor + parallel worker execution with pre-flight budget cap and graceful failure handling
- Intelligent model routing: cost_optimized, quality_first, balanced, fixed strategies
- Anthropic provider: Claude Haiku 4.5, Sonnet 4.6, Opus 4.8, Fable 5
- OpenAI provider: gpt-4o-mini, gpt-4o, gpt-4.1, o3
- Offline deterministic Echo provider (zero config, no API key required)
- Streaming token output through the event bus (TOKEN_STREAM_START, TOKEN_CHUNK, TOKEN_STREAM_END)
- Tool sandboxing with allowlist/denylist, per-tool timeouts, dangerous-denied-by-default
- RBAC with roles: admin, operator, developer, viewer
- Prompt-injection heuristics and input sanitization
- SHA-256 hash-chained tamper-evident audit log
- PII redaction: emails, cards, SSNs, IPs, phones
- Event bus with 21 lifecycle event types
- Per-run cost reporting (tokens + USD, per model, per agent)
- Conversation memory and in-memory RAG vector store
- CLI: forge run, forge models, forge audit
- 47 hermetic tests, mypy strict clean, ruff clean
- CI matrix across Python 3.11, 3.12, 3.13
- PyPI release via OIDC Trusted Publisher (pip install agentforge-oss)
