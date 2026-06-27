"""Intelligent model routing.

The router turns a *task description* (how hard is this, does it need tools?)
into a *concrete model choice*, subject to policy (allowed providers, which
providers are actually configured) and a routing strategy:

* ``cost_optimized`` — cheapest capable model.
* ``quality_first`` — highest-tier capable model.
* ``balanced`` — match model tier to task complexity, then minimise cost.
* ``fixed`` — always use the configured default model.

Routing is deterministic and never silently falls back to a model the caller
didn't make available — if nothing qualifies it raises
:class:`~forge.exceptions.ModelRoutingError`, which is far easier to debug than
a surprise bill on the wrong model.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel

from forge.config import RoutingConfig
from forge.exceptions import ModelRoutingError
from forge.models.registry import ModelInfo, ModelRegistry, ModelTier


class Complexity(enum.StrEnum):
    """How demanding a task is — drives tier selection in balanced mode."""

    TRIVIAL = "trivial"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def target_tier(self) -> ModelTier:
        return {
            "trivial": ModelTier.SMALL,
            "low": ModelTier.SMALL,
            "medium": ModelTier.MEDIUM,
            "high": ModelTier.LARGE,
        }[self.value]


class RoutingDecision(BaseModel):
    """The router's choice plus a human-readable justification."""

    model: str
    provider: str
    tier: ModelTier
    reason: str


class ModelRouter:
    """Selects a model for a task given a registry and routing policy."""

    def __init__(
        self,
        registry: ModelRegistry,
        config: RoutingConfig,
        *,
        available_providers: set[str] | None = None,
    ) -> None:
        self._registry = registry
        self._config = config
        #: Providers that are actually wired up. ``None`` means "don't filter".
        self._available = available_providers

    def _candidates(self, *, needs_tools: bool) -> list[ModelInfo]:
        """All models that satisfy the hard constraints (provider + tools)."""
        models = self._registry.all()
        if self._available is not None:
            models = [m for m in models if m.provider in self._available]
        if self._config.allow_providers is not None:
            allowed = set(self._config.allow_providers)
            models = [m for m in models if m.provider in allowed]
        if needs_tools:
            models = [m for m in models if m.supports_tools]
        return models

    def route(
        self,
        *,
        complexity: Complexity = Complexity.MEDIUM,
        needs_tools: bool = False,
        override: str | None = None,
    ) -> RoutingDecision:
        """Pick a model.

        ``override`` forces a specific model (still validated against
        availability/constraints). Otherwise the configured strategy decides.
        """
        candidates = self._candidates(needs_tools=needs_tools)
        if not candidates:
            raise ModelRoutingError(
                "No model satisfies the routing constraints",
                context={
                    "needs_tools": needs_tools,
                    "available_providers": sorted(self._available) if self._available else None,
                    "allow_providers": self._config.allow_providers,
                },
            )

        by_name = {m.name: m for m in candidates}

        # Explicit override wins, if it is actually usable.
        if override is not None:
            if override not in by_name:
                raise ModelRoutingError(
                    f"Requested model {override!r} is unavailable or unsupported here",
                    context={"candidates": sorted(by_name)},
                )
            return self._decide(by_name[override], f"explicit override -> {override}")

        strategy = self._config.strategy
        if strategy == "fixed":
            default = self._config.default_model
            if default not in by_name:
                raise ModelRoutingError(
                    f"Fixed strategy default model {default!r} is unavailable",
                    context={"candidates": sorted(by_name)},
                )
            return self._decide(by_name[default], f"fixed strategy -> {default}")

        if strategy == "cost_optimized":
            chosen = min(candidates, key=self._price)
            return self._decide(chosen, "cheapest capable model")

        if strategy == "quality_first":
            chosen = max(candidates, key=lambda m: (m.tier.rank, self._price(m)))
            return self._decide(chosen, "highest-tier capable model")

        # balanced (default): match tier to complexity, then minimise cost.
        target = complexity.target_tier
        chosen = min(
            candidates,
            key=lambda m: (abs(m.tier.rank - target.rank), self._price(m)),
        )
        return self._decide(
            chosen, f"balanced: complexity={complexity.value} -> tier~{target.value}"
        )

    @staticmethod
    def _price(model: ModelInfo) -> float:
        """A simple blended price used for cost comparisons."""
        return model.input_cost_per_mtok + model.output_cost_per_mtok

    @staticmethod
    def _decide(model: ModelInfo, reason: str) -> RoutingDecision:
        return RoutingDecision(
            model=model.name, provider=model.provider, tier=model.tier, reason=reason
        )
