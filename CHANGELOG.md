# Changelog

All notable changes to Forge are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Security
- **A token in a URL's query string was accepted and logged. Affects 0.5.5
  and earlier.** 0.5.5 refused credentials in a `base_url` but accepted
  `https://host/v1?key=...`, and the HTTP client logs the full request URL at
  INFO, so the token reached any log that captured INFO output. Every
  configured URL now follows one rule: credentials, fragments and query
  strings are refused, for the provider base URLs, the `OPENAI_BASE_URL`,
  `ANTHROPIC_BASE_URL` and `OLLAMA_BASE_URL` variables the SDKs read
  themselves (`ANTHROPIC_BASE_URL` was not checked before), and the
  OpenTelemetry endpoint (also not checked before). Errors never repeat the
  URL. If you ran 0.5.5 or earlier with a token in a URL and INFO logging on,
  treat the token as exposed and rotate it.
- **A failure's own text no longer leaves the local log.** When a worker
  failed, the supervisor put the exception's text into the synthesis prompt it
  sent to a model, and failure events and audit records stored it too. That
  text is whatever raised it: in 0.5.4 and earlier it could be a provider's
  reflected API key, and in any version it can be an upstream message, a URL or
  a value from a request. Events also reach a collector through the
  OpenTelemetry exporter. Synthesis prompts, the `run.finish` and
  `worker.failed` audit records, and the `model.call.failed`, `worker.failed`
  and `run.finished` events now carry only the exception type and a fixed
  description, such as `RuntimeError: worker failed`. The full text, with its
  traceback, is written to the local `forge.failures` log at WARNING.

### Added
- **`default_query`** on `OpenAIProvider` and `OpenAICompatibleProvider`, and
  `openai_compatible_default_query` (`FORGE_OPENAI_COMPATIBLE_DEFAULT_QUERY`,
  form-encoded) in the configuration, for parameters an endpoint requires on
  every request, such as an `api-version`. The values are sent but kept out of
  records and logs: `SecretStr` in the configuration, absent from provider
  descriptions, events and errors, and redacted from the log lines of the HTTP
  client and the vendor SDKs. No shipped preset needs one.

### Changed
- **`OllamaProvider` refuses credentials in its URL** instead of moving them
  into a Basic auth header, as 0.5.5 did. One rule now covers every configured
  URL. Migration: remove `user:password@` from `OLLAMA_BASE_URL`; if your
  Ollama sits behind an authenticating proxy, configure the proxy rather than
  the URL.
- The `error` field of those events and audit records is now
  `"<ExceptionType>: <fixed description>"`, and a new `error_type` field holds
  the type alone. Code that read the exception's message from them should read
  the local `forge.failures` log instead.

## [0.5.5] - 2026-11-24

Security release, with the OpenAI-compatible provider work. **Upgrade if you
use any network provider through a proxy, gateway or endpoint you do not
control**: see Security. Imports, public API and CLI commands are unchanged.
Two behaviours changed on purpose and are called out first under Changed:
Bedrock is no longer wired automatically from an instance profile, and
provider error messages no longer include upstream text.

### Added
- **`OpenAICompatibleProvider`** (`forge.models.providers.openai_compatible`,
  registry key `openai_compatible`): one generic provider for any endpoint that
  speaks the Chat Completions API, configured by a named preset, a `base_url`,
  or both. Vendor names appear only in the preset data, never in a module,
  class or CLI name.
- **Provider presets** as a versioned data file, `provider-presets.v1`, with a
  JSON Schema. Each preset records a `base_url`, a documentation link, the
  vendor's stated jurisdiction, and whether the endpoint is local or hosted.
  A preset is data, not a claim: no vendor API was contacted by this project,
  and the tests run against a fake in-process server only.
- Configuration fields `openai_compatible_base_url`,
  `openai_compatible_preset` and `openai_compatible_api_key_env` (environment:
  `FORGE_OPENAI_COMPATIBLE_BASE_URL`, `FORGE_OPENAI_COMPATIBLE_PRESET`,
  `FORGE_OPENAI_COMPATIBLE_API_KEY_ENV`). The last names the environment
  variable that holds the key; the key itself is never stored in configuration.
  With none of these set, nothing changes: the `openai` provider is built
  exactly as before.
- Every `model.call.started` event now carries `hosting`, `jurisdiction` and
  `provider_preset` beside `provider`, for every provider. Where nothing is
  known the value is the literal `"unknown"`, never an omission. Hosting for a
  custom `base_url` is derived from the endpoint, and its jurisdiction is
  `"unknown"`. `RunContext.policy_request_block(model)` returns the same facts
  in the `model` / `provider` shape a policy engine matches on.

- **User-supplied preset files.** `provider_presets_path`
  (`FORGE_PROVIDER_PRESETS_PATH`) names a `provider-presets.v1` file of your
  own, loaded alongside the built-in presets and validated the same way. A
  jurisdiction declared there is reported as attested, like the shipped ones.
  An id that collides with a built-in preset is an error, never a silent
  override.
- `model.call.started` events also carry `jurisdiction_verification`
  (`attested`, `unknown` or `not_applicable`) and `provider_preset_source`
  (`built_in` or `user`).
- `docs/provider-coverage.md` lists each preset's status. Ten of the eleven
  presets had their `base_url` read on the vendor's documentation on
  2026-11-24 and are marked `reviewed` with that date; one is left
  `unreviewed`. A review reads documentation only. No vendor API was
  contacted. The review corrected one preset's host and three documentation
  links, and marked one more preset `partial`.

### Security
- **`OpenAIProvider` and `OpenAICompatibleProvider` refuse a `base_url` that
  contains credentials. Affects 0.5.4 and earlier.** A URL such as
  `https://user:password@host/v1` was accepted and used, and the HTTP client
  logs the request URL at INFO on every call, so the password reached the
  logs. Such a URL now raises `ConfigurationError` at construction, for
  `base_url`, for a preset's URL and for the SDK's own `OPENAI_BASE_URL`. The
  error names the scheme and host only. Remove the credentials from the URL
  and supply the key through `api_key` or its environment variable. If you ran
  0.5.4 or earlier with credentials in a `base_url` and INFO logging on, treat
  them as exposed and rotate them.
- **`OpenAIProvider` error messages could contain the API key. Affects 0.5.4
  and earlier.** A failed request raised `ProviderResponseError` with the
  upstream error text interpolated into its message, and all three provider
  errors kept the SDK exception as their cause. A server that reflects request
  headers in its error body, which a misconfigured proxy or a third-party
  endpoint reached through `base_url` can do, therefore put the
  `Authorization` value into the exception message, into any traceback that
  rendered the cause chain, and into whatever logged or audited the error.

  Errors from this provider no longer carry upstream text. They carry the
  model, the exception type and the HTTP status code, and the cause chain is
  cut. If you logged or stored `ProviderResponseError` messages from 0.5.4 or
  earlier while pointing the provider at an endpoint you do not control,
  treat the key used there as exposed and rotate it.
- **`AnthropicProvider`, `BedrockProvider` and `OllamaProvider` errors could
  repeat upstream text, including credentials. Affects 0.5.4 and earlier.**
  The same fault as the `OpenAIProvider` one above, in the other three
  providers that call out:
  - `AnthropicProvider` interpolated the upstream error into
    `ProviderResponseError`, and all three of its errors kept the SDK
    exception as their cause. A server or proxy that reflects request headers
    could put the `x-api-key` value into the message or the traceback.
  - `BedrockProvider` interpolated the botocore `ClientError`, which carries
    the service's message, and kept it as the cause on every error path
    including `stream()`.
  - `OllamaProvider` repeated the response body of any non-200 answer and the
    text of any transport error, which includes the request URL, and reported
    its configured `base_url` verbatim in the error context. Ollama takes no
    API key, but a `base_url` with credentials in it (`http://user:pass@host`)
    exposed them this way.

  Errors from all five network providers now carry only values this library
  chose or measured: the model, the exception type, the HTTP status code, and
  for Bedrock the AWS error code when it has the shape of one. The cause chain
  is cut. One parametrized test runs every provider against an in-process
  server that reflects the request's credentials, for 401, 429 and 500. If you
  logged or stored provider error messages from 0.5.4 or earlier while using a
  proxy, gateway or endpoint you do not control, treat the credential used
  there as exposed and rotate it.
- **`OllamaProvider` no longer leaves URL credentials in the request URL.**
  Credentials in `base_url` are sent as the same Basic auth header as before
  but are removed from the URL, because the HTTP client logs every request URL
  at INFO. Affects 0.5.4 and earlier.
- Errors raised by `OpenAICompatibleProvider` never carry the upstream message
  text, only the endpoint origin and a status code, and the cause chain is cut,
  so an API key reflected by a server cannot reach an exception, a log line or
  an event.
- **The `http_get` tool no longer repeats exception text in its result.
  Affects 0.5.4 and earlier.** A failed request returned the HTTP client's
  error text to the model, and that text can repeat the requested URL,
  including a token in its query string or userinfo. The result now names the
  exception type only, and a URL that cannot be parsed returns an error
  instead of raising.

### Fixed
- **Constructing an `Orchestrator` no longer attempts a network call.** With
  boto3 installed, building the default providers asked boto3 to resolve AWS
  credentials, which walks the whole provider chain and ends at the
  instance-metadata address. Merely creating an orchestrator on a machine with
  no AWS configuration therefore tried to open a connection. Bedrock
  availability is now decided from local signals only: credential environment
  variables, a named profile, web-identity or container-credential variables,
  or the existence of a shared credentials file.

### Changed
- **Bedrock is no longer wired automatically from an instance profile.
  Migration: set `FORGE_BEDROCK_ENABLED=1` (or `bedrock_enabled = true`).**
  Before this release, building an `Orchestrator` asked boto3 to resolve AWS
  credentials, which ends at a network call to the instance-metadata service.
  That call is gone (see Fixed), so a host whose *only* AWS credentials come
  from an instance profile, such as an EC2 instance, ECS task or EKS pod role,
  no longer gets the Bedrock provider unless you ask for it. Hosts with
  credentials in environment variables or `~/.aws` files are detected as
  before and need no change. With the setting on, boto3 contacts the
  instance-metadata service when the provider is built, which is the lookup
  the setting asks for.
- Error messages from `AnthropicProvider`, `BedrockProvider` and
  `OllamaProvider` no longer include the upstream explanation (see Security).
  Code that matched on that text should read `error.context` instead:
  `status_code`, `error_type`, and `code` for Bedrock. Ollama's 400 and 404
  errors carry a fixed hint in place of the server's wording, and the
  `base_url` in its error context is now the scheme, host and port only.
- `OpenAIProvider` no longer decides between `max_tokens` and
  `max_completion_tokens` with a rule buried in the request path. The choice is
  now explicit data: a new `token_param` argument names the parameter to send
  for every model, and `completion_token_model_prefixes` (default `("o",)`)
  drives the per-model choice when `token_param` is unset. **Default behaviour
  against OpenAI's own API is unchanged**, and a test compares every default
  outcome with the previous rule. The reason for the change is other
  endpoints: reached through `base_url`, a model whose name merely starts with
  "o" was sent `max_completion_tokens`, which its server may reject. Callers
  that know their endpoint can now say which parameter it takes.

## [0.5.4] - 2026-08-12

Beta-hardening release. No new product features; imports, public API and CLI
commands are unchanged. One intentional behavior tightening is called out under
Changed below.

### Security & governance
- `ToolSandbox` now enforces tool RBAC against the run principal: normal tools
  require `USE_TOOL` and dangerous tools require `USE_DANGEROUS_TOOL`.
- Dangerous tools require **both** the `USE_DANGEROUS_TOOL` permission **and**
  explicit allowlisting before they may run.

### Budget reliability
- Added concurrent pre-call budget reservations (conservative input + maximum
  output) held under a lock, reducing the risk of parallel workers collectively
  oversubscribing a hard cap. Reservations settle against provider-reported
  usage on success and are released on failure — no permanent reservation leak
  and no double-counting. Costs remain local list-price estimates, not invoices.

### Audit reliability
- Redact actor and run identifiers (in addition to resource values) before
  writing audit records.
- Serialize in-process audit writes per audit file; `verify()` fails closed on
  malformed/unparseable records. Multi-process audit-file coordination remains a
  known limitation. Hash chains are tamper-evident, not immutable.

### Configuration
- Stricter validation: reject unknown configuration fields, malformed boolean
  environment values, non-positive tool timeouts, and invalid assignment.

### CLI
- Added `forge --version` / `forge -V`. The existing `forge version` command is
  unchanged.

### Packaging
- Declare supported Python as `>=3.11,<3.14`.
- Pin the PyPI Trusted Publishing action to `pypa/gh-action-pypi-publish`
  v1.14.2 (Twine 7 / Core Metadata 2.5 compatible).

### Changed
- **Ollama is no longer auto-detected.** Zero-configuration runs stay
  deterministic and offline (Echo). To enable the Ollama provider, set
  `OLLAMA_BASE_URL` explicitly (for a local or remote server).

## [0.5.3] - 2026-08-06

### Maintenance
- Publish corrected Apache-2.0 package metadata under the public maintainer identity `sekacorn`.
- Consolidated git commit authorship under a single maintainer identity (`sekacorn`).

## [0.5.2] — 2026-07-07

### Release infrastructure
- Reconnected release metadata for the recreated `sekacorn/AgentForge` repository.
- Updated package metadata to publish public author identity as `sekacorn`.
- Restored the dedicated GitHub Actions release workflow for PyPI Trusted Publishing.
- Added distribution validation and artifact handoff before publishing.

### Documentation
- Replaced stale version-specific shipped-feature wording with current release language.

## [0.5.1] — 2026-07-05

### Added
- PGVectorMemoryStore: durable RAG backend using PostgreSQL + pgvector with IVFFlat ANN indexing (cosine distance), asyncpg connection pool
- FORGE_PGVECTOR_DSN config for PostgreSQL connection string
- New extra: `pip install "agentforge-oss[pgvector]"` (asyncpg + pgvector-python)
- New example: examples/rag_pgvector.py

## [0.5.0] — 2026-07-01

### Added
- Policy-as-code governance: PolicyRule, PolicySet, PolicyDecision -- define deny/approve/log rules evaluated before every tool call
- Human-in-the-loop approval gates: async approver callbacks pause execution for human sign-off
- Four new event types: POLICY_EVALUATED, POLICY_APPROVED, POLICY_DENIED, POLICY_LOGGED
- New example: examples/policy_governance.py

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
