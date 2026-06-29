"""Typed configuration for Forge.

Configuration can come from three layers, applied in increasing priority:

1. Code defaults (the field defaults below).
2. A ``forge.toml`` file (optional; parsed with the stdlib ``tomllib``).
3. Environment variables (``FORGE_*`` plus the conventional provider keys
   ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``).

Everything is a Pydantic model so it validates eagerly and serializes cleanly
into audit records.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from forge.exceptions import ConfigurationError

RoutingStrategy = Literal["balanced", "cost_optimized", "quality_first", "fixed"]
MemoryBackend = Literal["inmemory", "sqlite"]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ConfigurationError(f"{name} must be a number, got {raw!r}") from exc


class RoutingConfig(BaseModel):
    """Controls how the :class:`~forge.models.router.ModelRouter` picks a model."""

    strategy: RoutingStrategy = "balanced"
    #: Used when ``strategy == "fixed"`` (always route here) or as a fallback.
    default_model: str = "echo-pro"
    #: Model used by supervisors for planning/aggregation when not overridden.
    planner_model: str | None = None
    #: If set, the router only considers models served by these providers.
    allow_providers: list[str] | None = None
    #: Preferred provider for routing. When set, the router restricts selection to
    #: this provider (others act as fallback only). The orchestrator auto-populates
    #: this from the available API keys when it is not set explicitly.
    default_provider: str | None = None


class BudgetConfig(BaseModel):
    """Hard limits that keep an autonomous run from running away with spend."""

    max_usd_per_run: float | None = None
    max_tokens_per_run: int | None = None
    #: Maximum reasoning/acting iterations a single agent may take.
    max_steps_per_agent: int = 12
    #: Maximum number of dynamic workers a supervisor may spawn for one goal.
    #: Also bounds parallelism: subtasks run in concurrent batches of this size.
    max_workers: int = 5


class SecurityConfig(BaseModel):
    """Guardrails applied to inputs and tool execution."""

    detect_prompt_injection: bool = True
    #: Inputs longer than this are rejected before reaching a model.
    max_input_chars: int = 200_000
    #: Per-tool execution timeout in seconds.
    tool_timeout_seconds: float = 30.0
    #: If set, only these tools may run (allowlist). ``None`` means "all".
    allow_tools: list[str] | None = None
    #: Tools that may never run (denylist), checked after the allowlist.
    block_tools: list[str] = Field(default_factory=list)


class ComplianceConfig(BaseModel):
    """Settings that support audit, governance and data-privacy requirements."""

    audit_enabled: bool = True
    audit_path: str = "audit/forge-audit.jsonl"
    #: Redact common PII patterns from prompts/outputs before logging.
    redact_pii: bool = True
    #: Optional data-residency hint recorded on every audit entry.
    data_region: str | None = None
    #: Optional retention window, surfaced to operators (enforcement is external).
    retention_days: int | None = None


class ObservabilityConfig(BaseModel):
    """Logging and tracing behaviour."""

    log_level: str = "INFO"
    #: Emit logs as JSON lines (recommended for production log aggregation).
    json_logs: bool = False


class ForgeConfig(BaseModel):
    """Top-level configuration object passed to the :class:`Orchestrator`."""

    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    #: Provider name -> API key. Populated from the environment by default.
    api_keys: dict[str, str] = Field(default_factory=dict)
    #: Base URL of a local Ollama server (overridable via ``OLLAMA_BASE_URL``). The
    #: orchestrator offers the Ollama provider when this is set explicitly or when a
    #: server is reachable here; see ``Orchestrator._build_default_providers``.
    ollama_base_url: str = "http://localhost:11434"
    #: Retrieval-memory backend (see ``forge.memory.build_memory``). ``"sqlite"``
    #: persists RAG state across restarts and needs the optional ``aiosqlite`` extra.
    memory_backend: MemoryBackend = "inmemory"
    #: Database file path for the SQLite memory backend.
    memory_path: str = "forge_memory.db"
    #: Export traces + metrics via OpenTelemetry (needs the optional ``otel`` extra).
    otel_enabled: bool = False
    #: OTLP endpoint (e.g. ``http://localhost:4317``); ``None`` exports to the console.
    otel_endpoint: str | None = None
    #: ``service.name`` resource attribute reported to the OTel backend.
    otel_service_name: str = "forge"

    @classmethod
    def load(cls, path: str | Path | None = None) -> ForgeConfig:
        """Build a config from (optional) TOML file overlaid with environment.

        If ``path`` is omitted, ``forge.toml`` in the current directory is used
        when present. Missing files are not an error.
        """
        data: dict[str, Any] = {}
        toml_path = Path(path) if path else Path("forge.toml")
        if toml_path.exists():
            with toml_path.open("rb") as fh:
                data = tomllib.load(fh)

        config = cls.model_validate(data)
        config._apply_environment()
        return config

    @classmethod
    def from_env(cls) -> ForgeConfig:
        """Convenience: defaults overlaid with environment variables only."""
        config = cls()
        config._apply_environment()
        return config

    def _apply_environment(self) -> None:
        """Overlay environment variables onto this config in place."""
        for provider, env_name in (
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("openai", "OPENAI_API_KEY"),
        ):
            value = os.environ.get(env_name)
            if value:
                self.api_keys[provider] = value

        if (ollama_url := os.environ.get("OLLAMA_BASE_URL")) is not None:
            self.ollama_base_url = ollama_url

        if (backend := os.environ.get("FORGE_MEMORY_BACKEND")) is not None:
            self.memory_backend = backend  # type: ignore[assignment]
        if (mem_path := os.environ.get("FORGE_MEMORY_PATH")) is not None:
            self.memory_path = mem_path

        self.otel_enabled = _env_bool("FORGE_OTEL_ENABLED", self.otel_enabled)
        if (otel_ep := os.environ.get("FORGE_OTEL_ENDPOINT")) is not None:
            self.otel_endpoint = otel_ep
        if (otel_sn := os.environ.get("FORGE_OTEL_SERVICE_NAME")) is not None:
            self.otel_service_name = otel_sn

        if (level := os.environ.get("FORGE_LOG_LEVEL")) is not None:
            self.observability.log_level = level
        self.observability.json_logs = _env_bool("FORGE_JSON_LOGS", self.observability.json_logs)

        if (model := os.environ.get("FORGE_DEFAULT_MODEL")) is not None:
            self.routing.default_model = model
        if (strategy := os.environ.get("FORGE_ROUTING_STRATEGY")) is not None:
            self.routing.strategy = strategy  # type: ignore[assignment]

        if (max_usd := _env_float("FORGE_MAX_USD_PER_RUN")) is not None:
            self.budget.max_usd_per_run = max_usd

        self.compliance.audit_enabled = _env_bool(
            "FORGE_AUDIT_ENABLED", self.compliance.audit_enabled
        )
        self.compliance.redact_pii = _env_bool("FORGE_REDACT_PII", self.compliance.redact_pii)
        if (audit_path := os.environ.get("FORGE_AUDIT_PATH")) is not None:
            self.compliance.audit_path = audit_path

    def api_key_for(self, provider: str) -> str | None:
        """Return the configured API key for ``provider``, if any."""
        return self.api_keys.get(provider)

    @property
    def anthropic_api_key(self) -> str | None:
        """The Anthropic API key, if configured (from ``ANTHROPIC_API_KEY``)."""
        return self.api_keys.get("anthropic")

    @property
    def openai_api_key(self) -> str | None:
        """The OpenAI API key, if configured (from ``OPENAI_API_KEY``)."""
        return self.api_keys.get("openai")
