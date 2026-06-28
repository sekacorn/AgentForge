"""The Orchestrator — Forge's top-level entry point.

The :class:`Orchestrator` owns the long-lived, shared services (providers,
router, sandbox, audit log, access control) and turns a plain-language *goal*
into a completed run. It applies the cross-cutting concerns in order:

1. **Access control** — the principal must be allowed to run agents.
2. **Input sanitization** — the goal is screened for prompt injection.
3. **Execution** — a single agent or a supervisor/worker team runs the goal.
4. **Accounting & audit** — usage/cost is tracked and the run is audit-logged.

Everything is async-first; ``run_sync`` is provided for scripts and the CLI.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel

from forge.agents.agent import Agent
from forge.agents.supervisor import Supervisor
from forge.compliance.audit import AuditLogger
from forge.compliance.redaction import PIIRedactor
from forge.config import ForgeConfig
from forge.exceptions import ConfigurationError, ForgeError
from forge.models.base import ModelProvider
from forge.models.providers.anthropic import AnthropicProvider
from forge.models.providers.echo import EchoProvider
from forge.models.providers.openai import OpenAIProvider
from forge.models.registry import ModelRegistry
from forge.models.router import ModelRouter
from forge.observability.events import Event, EventBus, EventType
from forge.observability.logging import configure_logging, get_logger
from forge.observability.usage import UsageReport, UsageTracker
from forge.orchestration.context import RunContext
from forge.security.access import AccessController, Permission, Principal
from forge.security.sandbox import ToolSandbox
from forge.security.sanitization import InputSanitizer
from forge.tools.base import Tool
from forge.tools.builtin import SAFE_TOOLS
from forge.tools.registry import ToolRegistry
from forge.types import new_id

RunMode = Literal["supervisor", "single", "auto"]


class RunResult(BaseModel):
    """The result of an orchestrated run."""

    run_id: str
    goal: str
    output: str
    usage: UsageReport
    success: bool = True


class Orchestrator:
    """Coordinates agents, providers, governance and observability for a run."""

    def __init__(
        self,
        config: ForgeConfig | None = None,
        *,
        providers: dict[str, ModelProvider] | None = None,
        registry: ModelRegistry | None = None,
        tools: ToolRegistry | Iterable[Tool] | None = None,
        principal: Principal | None = None,
        access: AccessController | None = None,
    ) -> None:
        self.config = config or ForgeConfig.from_env()
        configure_logging(
            self.config.observability.log_level, json_logs=self.config.observability.json_logs
        )
        self._log = get_logger("orchestrator")

        self.registry = registry or ModelRegistry()
        self.providers = providers if providers is not None else self._build_default_providers()
        if not self.providers:
            raise ConfigurationError("No model providers are configured")
        self._apply_default_provider()

        self.router = ModelRouter(
            self.registry, self.config.routing, available_providers=set(self.providers)
        )
        self.events = EventBus()
        self.sandbox = ToolSandbox(self.config.security, events=self.events)
        self.redactor = PIIRedactor(self.config.compliance.redact_pii)
        self.audit = AuditLogger(
            self.config.compliance.audit_path,
            enabled=self.config.compliance.audit_enabled,
            region=self.config.compliance.data_region,
            redactor=self.redactor,
        )
        self.sanitizer = InputSanitizer(self.config.security)
        self.access = access or AccessController()
        self.principal = principal or Principal.system()
        self.default_tools = (
            self._resolve_tools(tools) if tools is not None else ToolRegistry(list(SAFE_TOOLS))
        )

        self._log.info(
            "Forge orchestrator ready (providers=%s, models=%d)",
            ", ".join(sorted(self.providers)),
            len(self.registry.all()),
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def subscribe(self, handler) -> None:  # type: ignore[no-untyped-def]
        """Register an event handler (see :class:`~forge.observability.events.Event`)."""
        self.events.subscribe(handler)

    async def run(
        self,
        goal: str,
        *,
        mode: RunMode = "supervisor",
        tools: ToolRegistry | Iterable[Tool] | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
        principal: Principal | None = None,
        stream: bool = False,
    ) -> RunResult:
        """Run ``goal`` to completion and return a :class:`RunResult`.

        Parameters
        ----------
        mode:
            ``"supervisor"`` (default) decomposes the goal across a worker team;
            ``"single"`` runs one agent. ``"auto"`` is an alias for supervisor.
        tools:
            Tools available to agents this run (defaults to the orchestrator's).
        system_prompt:
            Optional system prompt for the agent(s).
        model:
            Force a specific model id, bypassing routing strategy.
        principal:
            The actor on whose behalf the run executes (for RBAC + audit).
        stream:
            When True, agents stream model output token-by-token through the event
            bus (subscribe for ``TOKEN_CHUNK`` events). Default False is identical
            to the non-streaming path.
        """
        actor = principal or self.principal
        self.access.require(actor, Permission.RUN_AGENT)
        clean_goal = self.sanitizer.check(goal)

        run_id = new_id("run")
        usage = UsageTracker()
        ctx = RunContext(
            run_id=run_id,
            config=self.config,
            registry=self.registry,
            router=self.router,
            providers=self.providers,
            sandbox=self.sandbox,
            usage=usage,
            events=self.events,
            audit=self.audit,
            stream=stream,
        )
        tools_registry = self._resolve_tools(tools) if tools is not None else self.default_tools

        self.events.emit(EventType.RUN_STARTED, run_id=run_id, mode=mode, stream=stream)
        self.audit.record(
            "run.start", actor=actor.id, run_id=run_id, resource=mode, goal=clean_goal[:200]
        )

        try:
            if mode == "single":
                agent = Agent(
                    "agent",
                    ctx,
                    system_prompt=system_prompt,
                    tools=tools_registry,
                    model_override=model,
                )
                result = await agent.run(clean_goal)
            elif mode in ("supervisor", "auto"):
                supervisor = Supervisor(
                    "supervisor",
                    ctx,
                    worker_tools=tools_registry,
                    system_prompt=system_prompt,
                    model_override=model,
                )
                result = await supervisor.run(clean_goal)
            else:  # pragma: no cover - guarded by typing
                raise ConfigurationError(f"Unknown run mode {mode!r}")
        except Exception as exc:
            # Audit and surface *any* failure (not only ForgeError) so the run's
            # outcome is always recorded, then re-raise for the caller to handle.
            self.events.emit(EventType.RUN_FINISHED, run_id=run_id, success=False, error=str(exc))
            self.audit.record(
                "run.finish", actor=actor.id, run_id=run_id, outcome="error", error=str(exc)
            )
            raise

        report = usage.report()
        self.events.emit(
            EventType.RUN_FINISHED,
            run_id=run_id,
            success=True,
            cost_usd=report.total.cost_usd,
            tokens=report.total.total_tokens,
        )
        self.audit.record(
            "run.finish",
            actor=actor.id,
            run_id=run_id,
            outcome="ok",
            cost_usd=report.total.cost_usd,
            tokens=report.total.total_tokens,
        )
        return RunResult(
            run_id=run_id, goal=clean_goal, output=result.output, usage=report, success=True
        )

    def run_sync(self, goal: str, **kwargs: object) -> RunResult:
        """Synchronous wrapper around :meth:`run` for scripts and the CLI."""
        return asyncio.run(self.run(goal, **kwargs))  # type: ignore[arg-type]

    def verify_audit(self) -> bool:
        """Verify the integrity of the audit log's hash chain."""
        return self.audit.verify()

    async def aclose(self) -> None:
        """Release provider resources (HTTP clients, etc.)."""
        for provider in self.providers.values():
            await provider.aclose()

    async def __aenter__(self) -> Orchestrator:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _build_default_providers(self) -> dict[str, ModelProvider]:
        """Always provide the offline echo provider; add Anthropic/OpenAI if keyed."""
        providers: dict[str, ModelProvider] = {"echo": EchoProvider()}
        anthropic_key = self.config.api_key_for("anthropic")
        if anthropic_key:
            try:
                providers["anthropic"] = AnthropicProvider(api_key=anthropic_key)
            except (ForgeError, ImportError) as exc:
                self._log.warning("Anthropic provider unavailable: %s", exc)
        openai_key = self.config.api_key_for("openai")
        if openai_key:
            try:
                providers["openai"] = OpenAIProvider(api_key=openai_key)
            except (ForgeError, ImportError) as exc:
                self._log.warning("OpenAI provider unavailable: %s", exc)
        return providers

    def _apply_default_provider(self) -> None:
        """Choose a default provider so real work is not shadowed by free echo models.

        Precedence: explicit user config > Anthropic > OpenAI > Echo. The chosen
        provider's cheapest model is used for the lightweight planning pass; echo
        stays available as the offline fallback.
        """
        routing = self.config.routing
        if routing.default_provider is not None or routing.allow_providers is not None:
            return  # respect explicit user routing configuration
        available = set(self.providers)
        if "anthropic" in available:
            default_provider, cheapest = "anthropic", "claude-haiku-4-5"
        elif "openai" in available:
            default_provider, cheapest = "openai", "gpt-4o-mini"
        else:
            return  # echo-only: keep the offline defaults
        routing.default_provider = default_provider
        if routing.planner_model is None and self.registry.has(cheapest):
            routing.planner_model = cheapest

    @staticmethod
    def _resolve_tools(tools: ToolRegistry | Iterable[Tool]) -> ToolRegistry:
        if isinstance(tools, ToolRegistry):
            return tools
        return ToolRegistry(list(tools))


__all__ = ["Orchestrator", "RunResult", "RunMode", "Event"]
