# Configuration

Configuration comes from three layers, applied in increasing priority:

1. **Code defaults** (the field defaults in `ForgeConfig`)
2. **`forge.toml`** file (optional; parsed with the stdlib `tomllib`)
3. **Environment variables** (`FORGE_*` plus the conventional provider keys)

A higher-priority layer overrides a lower one. If no `forge.toml` exists, Forge
runs entirely from defaults and environment variables.

---

## forge.toml

```toml
[routing]
strategy = "balanced"          # cost_optimized | quality_first | balanced | fixed
default_model = "claude-opus-4-8"

[budget]
max_usd_per_run = 0.50
max_steps_per_agent = 12
max_workers = 6

[security]
detect_prompt_injection = true
tool_timeout_seconds = 30
# allow_tools = ["calculator", "http_get"]   # uncomment to permit a dangerous tool

[compliance]
audit_enabled = true
redact_pii = true
data_region = "eu-west-1"
```

---

## Environment variables

### Provider keys

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude models |
| `OPENAI_API_KEY` | OpenAI API key for GPT models |
| `OLLAMA_BASE_URL` | Ollama server URL (default: `http://localhost:11434`) |
| `AWS_REGION` | AWS region for Bedrock (default: `us-east-1`) |
| `AWS_PROFILE` | AWS named profile for Bedrock |

### Routing

| Variable | Description | Default |
|---|---|---|
| `FORGE_DEFAULT_MODEL` | Default model alias | `echo-pro` |
| `FORGE_ROUTING_STRATEGY` | Routing strategy | `balanced` |

### Budget

| Variable | Description | Default |
|---|---|---|
| `FORGE_MAX_USD_PER_RUN` | Hard USD cap per run | None (unlimited) |

### Memory

| Variable | Description | Default |
|---|---|---|
| `FORGE_MEMORY_BACKEND` | Memory backend: `inmemory`, `sqlite`, `pgvector` | `inmemory` |
| `FORGE_MEMORY_PATH` | SQLite database file path | `forge_memory.db` |
| `FORGE_PGVECTOR_DSN` | PostgreSQL connection string for pgvector | None |

### Observability

| Variable | Description | Default |
|---|---|---|
| `FORGE_LOG_LEVEL` | Log level (DEBUG, INFO, WARNING, ERROR) | `INFO` |
| `FORGE_JSON_LOGS` | Emit logs as JSON lines | `false` |
| `FORGE_OTEL_ENABLED` | Enable OpenTelemetry export | `false` |
| `FORGE_OTEL_ENDPOINT` | OTLP endpoint URL | None (console) |
| `FORGE_OTEL_SERVICE_NAME` | OTel service.name attribute | `forge` |

### Compliance

| Variable | Description | Default |
|---|---|---|
| `FORGE_AUDIT_ENABLED` | Enable the audit log | `true` |
| `FORGE_AUDIT_PATH` | Audit log file path | `audit/forge-audit.jsonl` |
| `FORGE_REDACT_PII` | Redact PII from logs and audit | `true` |

---

## Programmatic configuration

```python
from forge import ForgeConfig, BudgetConfig, RoutingConfig

config = ForgeConfig(
    routing=RoutingConfig(strategy="cost_optimized"),
    budget=BudgetConfig(max_usd_per_run=0.25, max_workers=3),
)
```

Or load from file + environment:

```python
config = ForgeConfig.load("forge.toml")  # file + env overlay
config = ForgeConfig.from_env()          # env only, no file
```
