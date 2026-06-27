from __future__ import annotations

import pytest

from forge import Complexity, ConfigurationError, ModelRegistry, ModelRouter, Usage
from forge.config import RoutingConfig


def test_registry_unknown_model_raises() -> None:
    registry = ModelRegistry()
    with pytest.raises(ConfigurationError):
        registry.get("does-not-exist")


def test_opus_pricing_matches_list_price() -> None:
    registry = ModelRegistry()
    # 1M input tokens at $5/1M = $5.00 exactly.
    cost = registry.cost(Usage(input_tokens=1_000_000), "claude-opus-4-8")
    assert cost == pytest.approx(5.0)
    # 1M output tokens at $25/1M = $25.00.
    cost_out = registry.cost(Usage(output_tokens=1_000_000), "claude-opus-4-8")
    assert cost_out == pytest.approx(25.0)


def _router(strategy: str) -> ModelRouter:
    return ModelRouter(
        ModelRegistry(),
        RoutingConfig(strategy=strategy, default_model="claude-opus-4-8"),
        available_providers={"anthropic"},
    )


def test_cost_optimized_picks_cheapest() -> None:
    decision = _router("cost_optimized").route()
    assert decision.model == "claude-haiku-4-5"


def test_quality_first_picks_frontier() -> None:
    decision = _router("quality_first").route()
    assert decision.model == "claude-fable-5"


def test_balanced_matches_complexity_to_tier() -> None:
    router = _router("balanced")
    assert router.route(complexity=Complexity.LOW).model == "claude-haiku-4-5"
    assert router.route(complexity=Complexity.MEDIUM).model == "claude-sonnet-4-6"
    assert router.route(complexity=Complexity.HIGH).model == "claude-opus-4-8"


def test_fixed_uses_default_model() -> None:
    assert _router("fixed").route().model == "claude-opus-4-8"


def test_override_must_be_available() -> None:
    from forge import ModelRoutingError

    router = _router("balanced")
    assert router.route(override="claude-sonnet-4-6").model == "claude-sonnet-4-6"
    with pytest.raises(ModelRoutingError):
        router.route(override="echo-mini")  # provider not available here
