"""The shared per-run context handed to agents.

A :class:`RunContext` bundles every service an agent needs — the router, the
configured providers, the tool sandbox, usage tracking, the event bus, and the
audit log — into one object scoped to a single run. Shared, stateless services
(router, providers) are reused across runs; per-run state (``usage``, ``run_id``)
is fresh each time. Centralising budget enforcement here keeps that policy in one
place rather than scattered through the agents.
"""

from __future__ import annotations

from dataclasses import dataclass

from forge.compliance.audit import AuditLogger
from forge.config import ForgeConfig
from forge.exceptions import BudgetExceededError, ConfigurationError
from forge.models.base import ModelProvider
from forge.models.registry import ModelRegistry
from forge.models.router import ModelRouter
from forge.observability.events import EventBus, EventType
from forge.observability.usage import UsageTracker, estimate_worker_batch_cost
from forge.security.sandbox import ToolSandbox


@dataclass
class RunContext:
    """Services and state shared by all agents within one run."""

    run_id: str
    config: ForgeConfig
    registry: ModelRegistry
    router: ModelRouter
    providers: dict[str, ModelProvider]
    sandbox: ToolSandbox
    usage: UsageTracker
    events: EventBus
    audit: AuditLogger
    #: When True, agents stream model output token-by-token through the event bus
    #: (TOKEN_STREAM_START / TOKEN_CHUNK / TOKEN_STREAM_END) instead of awaiting the
    #: whole completion. Defaults to False so the standard path is unchanged.
    stream: bool = False

    def provider_for(self, model_name: str) -> ModelProvider:
        """Resolve the provider instance that serves ``model_name``."""
        info = self.registry.get(model_name)
        try:
            return self.providers[info.provider]
        except KeyError as exc:
            raise ConfigurationError(
                f"No provider configured for {info.provider!r} (needed by model {model_name!r})",
                context={"configured": sorted(self.providers)},
            ) from exc

    def check_budget(self) -> None:
        """Enforce per-run cost/token budgets; raise when a hard limit is crossed.

        Emits a :data:`EventType.BUDGET_WARNING` once spend passes 80% of a
        limit so operators get a heads-up before the run is halted.
        """
        budget = self.config.budget
        total = self.usage.total

        if budget.max_usd_per_run is not None:
            if total.cost_usd > budget.max_usd_per_run:
                self.events.emit(
                    EventType.BUDGET_EXCEEDED,
                    run_id=self.run_id,
                    kind="cost",
                    spent_usd=total.cost_usd,
                    limit_usd=budget.max_usd_per_run,
                )
                raise BudgetExceededError(
                    "Run exceeded its USD budget",
                    context={"spent_usd": total.cost_usd, "limit_usd": budget.max_usd_per_run},
                )
            if total.cost_usd >= 0.8 * budget.max_usd_per_run:
                self.events.emit(
                    EventType.BUDGET_WARNING,
                    run_id=self.run_id,
                    kind="cost",
                    spent_usd=total.cost_usd,
                    limit_usd=budget.max_usd_per_run,
                )

        if budget.max_tokens_per_run is not None and total.total_tokens > budget.max_tokens_per_run:
            self.events.emit(
                EventType.BUDGET_EXCEEDED,
                run_id=self.run_id,
                kind="tokens",
                spent_tokens=total.total_tokens,
                limit_tokens=budget.max_tokens_per_run,
            )
            raise BudgetExceededError(
                "Run exceeded its token budget",
                context={
                    "spent_tokens": total.total_tokens,
                    "limit_tokens": budget.max_tokens_per_run,
                },
            )

    def preflight_budget(self, *, num_workers: int, model_name: str) -> None:
        """Conservative pre-flight budget guard, run BEFORE a batch of workers spawns.

        Estimates the worst-case spend of the upcoming batch and refuses to start
        it if that would exceed the remaining USD budget. This is intentionally
        pessimistic (it assumes every worker uses its full step budget), so under
        parallelism it errs toward stopping early rather than overspending. The
        precise, real-time guard remains :meth:`check_budget`, run after every
        model call inside each worker.
        """
        budget = self.config.budget
        max_usd = budget.max_usd_per_run
        if max_usd is None:
            return

        info = self.registry.get(model_name)
        estimated = estimate_worker_batch_cost(
            num_workers=num_workers,
            input_cost_per_mtok=info.input_cost_per_mtok,
            max_steps_per_agent=budget.max_steps_per_agent,
        )
        remaining = max_usd - self.usage.total.cost_usd
        if estimated > remaining:
            self.events.emit(
                EventType.BUDGET_EXCEEDED,
                run_id=self.run_id,
                kind="preflight",
                estimated_usd=round(estimated, 6),
                remaining_usd=round(remaining, 6),
                num_workers=num_workers,
                model=model_name,
            )
            raise BudgetExceededError(
                "Pre-flight budget check: estimated worker-batch spend exceeds remaining budget",
                context={
                    "estimated_usd": round(estimated, 6),
                    "remaining_usd": round(remaining, 6),
                    "num_workers": num_workers,
                    "model": model_name,
                },
            )
