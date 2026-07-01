# Configuration reference

Every config field in every section, with type, default, and environment variable.

---

## RoutingConfig

Controls how the `ModelRouter` picks a model.

| Field | Type | Default | Env var |
|---|---|---|---|
| `strategy` | `"balanced" \| "cost_optimized" \| "quality_first" \| "fixed"` | `"balanced"` | `FORGE_ROUTING_STRATEGY` |
| `default_model` | `str` | `"echo-pro"` | `FORGE_DEFAULT_MODEL` |
| `planner_model` | `str \| None` | `None` | -- |
| `allow_providers` | `list[str] \| None` | `None` | -- |
| `default_provider` | `str \| None` | `None` | -- |

---

## BudgetConfig

Hard limits that keep an autonomous run from running away with spend.

| Field | Type | Default | Env var |
|---|---|---|---|
| `max_usd_per_run` | `float \| None` | `None` | `FORGE_MAX_USD_PER_RUN` |
| `max_tokens_per_run` | `int \| None` | `None` | -- |
| `max_steps_per_agent` | `int` | `12` | -- |
| `max_workers` | `int` | `5` | -- |

---

## SecurityConfig

Guardrails applied to inputs and tool execution.

| Field | Type | Default | Env var |
|---|---|---|---|
| `detect_prompt_injection` | `bool` | `True` | -- |
| `max_input_chars` | `int` | `200000` | -- |
| `tool_timeout_seconds` | `float` | `30.0` | -- |
| `allow_tools` | `list[str] \| None` | `None` | -- |
| `block_tools` | `list[str]` | `[]` | -- |

---

## ComplianceConfig

Settings that support audit, governance, and data-privacy requirements.

| Field | Type | Default | Env var |
|---|---|---|---|
| `audit_enabled` | `bool` | `True` | `FORGE_AUDIT_ENABLED` |
| `audit_path` | `str` | `"audit/forge-audit.jsonl"` | `FORGE_AUDIT_PATH` |
| `redact_pii` | `bool` | `True` | `FORGE_REDACT_PII` |
| `data_region` | `str \| None` | `None` | -- |
| `retention_days` | `int \| None` | `None` | -- |

---

## ObservabilityConfig

Logging and tracing behavior.

| Field | Type | Default | Env var |
|---|---|---|---|
| `log_level` | `str` | `"INFO"` | `FORGE_LOG_LEVEL` |
| `json_logs` | `bool` | `False` | `FORGE_JSON_LOGS` |

---

## Top-level ForgeConfig fields

| Field | Type | Default | Env var |
|---|---|---|---|
| `routing` | `RoutingConfig` | See above | -- |
| `budget` | `BudgetConfig` | See above | -- |
| `security` | `SecurityConfig` | See above | -- |
| `compliance` | `ComplianceConfig` | See above | -- |
| `observability` | `ObservabilityConfig` | See above | -- |
| `api_keys` | `dict[str, SecretStr]` | `{}` | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` |
| `ollama_base_url` | `SecretStr` | `"http://localhost:11434"` | `OLLAMA_BASE_URL` |
| `memory_backend` | `"inmemory" \| "sqlite" \| "pgvector"` | `"inmemory"` | `FORGE_MEMORY_BACKEND` |
| `memory_path` | `str` | `"forge_memory.db"` | `FORGE_MEMORY_PATH` |
| `pgvector_dsn` | `str \| None` | `None` | `FORGE_PGVECTOR_DSN` |
| `otel_enabled` | `bool` | `False` | `FORGE_OTEL_ENABLED` |
| `otel_endpoint` | `str \| None` | `None` | `FORGE_OTEL_ENDPOINT` |
| `otel_service_name` | `str` | `"forge"` | `FORGE_OTEL_SERVICE_NAME` |
| `bedrock_region` | `str \| None` | `None` | `AWS_REGION`, `BEDROCK_REGION` |
| `bedrock_profile` | `str \| None` | `None` | `AWS_PROFILE` |

---

## Priority order

Configuration layers, from lowest to highest priority:

1. **Code defaults** -- the field defaults in `ForgeConfig`
2. **`forge.toml`** -- optional TOML file parsed with stdlib `tomllib`
3. **Environment variables** -- `FORGE_*` and provider keys

A higher-priority layer overrides a lower one.

---

## Loading config

```python
from forge import ForgeConfig

# From file + environment overlay
config = ForgeConfig.load("forge.toml")

# From environment only (no file)
config = ForgeConfig.from_env()

# Programmatic (code defaults only)
config = ForgeConfig(memory_backend="sqlite")
```
