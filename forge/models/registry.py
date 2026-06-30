"""The model registry: capabilities and pricing in one authoritative place.

Each :class:`ModelInfo` records what a model can do (context window, tool
support, tier) and what it costs. Pricing is expressed per million tokens to
match how providers publish it. Keeping cost here — rather than in providers —
means the router can reason about price/quality trade-offs and the usage tracker
can compute spend consistently regardless of which provider served a call.

Prices below are list prices in USD per 1M tokens and are easy to update as
providers change them; nothing else in the codebase hardcodes a price.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel

from forge.exceptions import ConfigurationError
from forge.types import Usage


class ModelTier(enum.StrEnum):
    """A coarse capability/price band used by the router."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    FRONTIER = "frontier"

    @property
    def rank(self) -> int:
        return {"small": 0, "medium": 1, "large": 2, "frontier": 3}[self.value]


class ModelInfo(BaseModel):
    """Static metadata and pricing for a single model."""

    name: str
    provider: str
    tier: ModelTier
    context_window: int
    max_output_tokens: int
    input_cost_per_mtok: float
    output_cost_per_mtok: float
    supports_tools: bool = True
    supports_thinking: bool = False
    description: str = ""

    def cost(self, usage: Usage) -> float:
        """Compute the USD cost of ``usage`` for this model.

        Cache reads are billed at ~0.1x input and cache writes at ~1.25x input,
        matching common provider pricing for prompt caching.
        """
        m = 1_000_000
        billed_input = usage.input_tokens + usage.cache_write_tokens * 1.25
        billed_cache_read = usage.cache_read_tokens * 0.1
        cost = (
            (billed_input + billed_cache_read) * self.input_cost_per_mtok
            + usage.output_tokens * self.output_cost_per_mtok
        ) / m
        return round(cost, 8)


def _default_models() -> list[ModelInfo]:
    """The models Forge knows about out of the box.

    The ``echo`` models are free, deterministic, and require no API key — they
    power the tests and the offline quickstart. The Claude models carry real
    list pricing and capabilities.
    """
    return [
        ModelInfo(
            name="echo-mini",
            provider="echo",
            tier=ModelTier.SMALL,
            context_window=128_000,
            max_output_tokens=8_192,
            input_cost_per_mtok=0.0,
            output_cost_per_mtok=0.0,
            supports_tools=True,
            description="Deterministic offline model for tests and demos (cheap tier).",
        ),
        ModelInfo(
            name="echo-pro",
            provider="echo",
            tier=ModelTier.LARGE,
            context_window=128_000,
            max_output_tokens=8_192,
            input_cost_per_mtok=0.0,
            output_cost_per_mtok=0.0,
            supports_tools=True,
            description="Deterministic offline model for tests and demos (large tier).",
        ),
        ModelInfo(
            name="claude-haiku-4-5",
            provider="anthropic",
            tier=ModelTier.SMALL,
            context_window=200_000,
            max_output_tokens=64_000,
            input_cost_per_mtok=1.0,
            output_cost_per_mtok=5.0,
            supports_tools=True,
            description="Fastest, most cost-effective Claude model for simple tasks.",
        ),
        ModelInfo(
            name="claude-sonnet-4-6",
            provider="anthropic",
            tier=ModelTier.MEDIUM,
            context_window=1_000_000,
            max_output_tokens=64_000,
            input_cost_per_mtok=3.0,
            output_cost_per_mtok=15.0,
            supports_tools=True,
            supports_thinking=True,
            description="Best balance of speed and intelligence for high-volume work.",
        ),
        ModelInfo(
            name="claude-opus-4-8",
            provider="anthropic",
            tier=ModelTier.LARGE,
            context_window=1_000_000,
            max_output_tokens=128_000,
            input_cost_per_mtok=5.0,
            output_cost_per_mtok=25.0,
            supports_tools=True,
            supports_thinking=True,
            description="Most capable Opus-tier model for autonomous, long-horizon work.",
        ),
        ModelInfo(
            name="claude-fable-5",
            provider="anthropic",
            tier=ModelTier.FRONTIER,
            context_window=1_000_000,
            max_output_tokens=128_000,
            input_cost_per_mtok=10.0,
            output_cost_per_mtok=50.0,
            supports_tools=True,
            supports_thinking=True,
            description="Most capable model for the most demanding reasoning tasks.",
        ),
        ModelInfo(
            name="gpt-4o-mini",
            provider="openai",
            tier=ModelTier.SMALL,
            context_window=128_000,
            max_output_tokens=16_384,
            input_cost_per_mtok=0.15,
            output_cost_per_mtok=0.60,
            supports_tools=True,
            description="Fast, low-cost OpenAI model for simple, high-volume tasks.",
        ),
        ModelInfo(
            name="gpt-4o",
            provider="openai",
            tier=ModelTier.MEDIUM,
            context_window=128_000,
            max_output_tokens=16_384,
            input_cost_per_mtok=2.50,
            output_cost_per_mtok=10.0,
            supports_tools=True,
            description="Balanced OpenAI multimodal model for general-purpose work.",
        ),
        ModelInfo(
            name="gpt-4.1",
            provider="openai",
            tier=ModelTier.LARGE,
            context_window=1_000_000,
            max_output_tokens=32_768,
            input_cost_per_mtok=2.0,
            output_cost_per_mtok=8.0,
            supports_tools=True,
            description="Large-context OpenAI model for demanding, long-horizon work.",
        ),
        ModelInfo(
            name="o3",
            provider="openai",
            tier=ModelTier.FRONTIER,
            context_window=200_000,
            max_output_tokens=100_000,
            input_cost_per_mtok=10.0,
            output_cost_per_mtok=40.0,
            supports_tools=True,
            supports_thinking=True,
            description="OpenAI reasoning model for the most demanding problems.",
        ),
        # These run on a local Ollama server and cost nothing. Each must be pulled
        # locally first, e.g. ``ollama pull llama3.1:8b``, before it can be used.
        ModelInfo(
            name="llama3.2:3b",
            provider="ollama",
            tier=ModelTier.SMALL,
            context_window=128_000,
            max_output_tokens=8_192,
            input_cost_per_mtok=0.0,
            output_cost_per_mtok=0.0,
            supports_tools=True,
            description="Small, fast local Llama model for simple tasks (pull first).",
        ),
        ModelInfo(
            name="llama3.1:8b",
            provider="ollama",
            tier=ModelTier.MEDIUM,
            context_window=128_000,
            max_output_tokens=8_192,
            input_cost_per_mtok=0.0,
            output_cost_per_mtok=0.0,
            supports_tools=True,
            description="Balanced local Llama model with tool calling (pull first).",
        ),
        ModelInfo(
            name="llama3.1:70b",
            provider="ollama",
            tier=ModelTier.LARGE,
            context_window=128_000,
            max_output_tokens=8_192,
            input_cost_per_mtok=0.0,
            output_cost_per_mtok=0.0,
            supports_tools=True,
            description="Large local Llama model for demanding local work (pull first).",
        ),
        ModelInfo(
            name="mistral:7b",
            provider="ollama",
            tier=ModelTier.SMALL,
            context_window=32_000,
            max_output_tokens=8_192,
            input_cost_per_mtok=0.0,
            output_cost_per_mtok=0.0,
            supports_tools=True,
            description="Compact local Mistral model for fast, simple tasks (pull first).",
        ),
        ModelInfo(
            name="qwen2.5:7b",
            provider="ollama",
            tier=ModelTier.MEDIUM,
            context_window=128_000,
            max_output_tokens=8_192,
            input_cost_per_mtok=0.0,
            output_cost_per_mtok=0.0,
            supports_tools=True,
            description="Capable local Qwen model with tool calling (pull first).",
        ),
        ModelInfo(
            name="deepseek-r1:8b",
            provider="ollama",
            tier=ModelTier.MEDIUM,
            context_window=64_000,
            max_output_tokens=8_192,
            input_cost_per_mtok=0.0,
            output_cost_per_mtok=0.0,
            supports_tools=False,
            supports_thinking=True,
            description="Local DeepSeek-R1 reasoning model (no tool calling; pull first).",
        ),
        # -- Amazon Bedrock (Converse API) --------------------------------- #
        # Models run inside the customer's own AWS account/region via boto3.
        # Prices reflect typical us-east-1 list pricing and may vary by region —
        # this is a known limitation; Forge does not fetch live Bedrock pricing.
        ModelInfo(
            name="anthropic.claude-3-5-haiku-20241022-v1:0",
            provider="bedrock",
            tier=ModelTier.SMALL,
            context_window=200_000,
            max_output_tokens=8_192,
            input_cost_per_mtok=0.80,
            output_cost_per_mtok=4.00,
            supports_tools=True,
            description="Claude 3.5 Haiku on Bedrock: fast, low-cost in-account model.",
        ),
        ModelInfo(
            name="anthropic.claude-3-5-sonnet-20241022-v2:0",
            provider="bedrock",
            tier=ModelTier.MEDIUM,
            context_window=200_000,
            max_output_tokens=8_192,
            input_cost_per_mtok=3.00,
            output_cost_per_mtok=15.00,
            supports_tools=True,
            description="Claude 3.5 Sonnet on Bedrock: balanced in-account model.",
        ),
        ModelInfo(
            name="meta.llama3-1-70b-instruct-v1:0",
            provider="bedrock",
            tier=ModelTier.MEDIUM,
            context_window=128_000,
            max_output_tokens=4_096,
            input_cost_per_mtok=0.72,
            output_cost_per_mtok=0.72,
            supports_tools=True,
            description="Llama 3.1 70B on Bedrock: open-weight in-account model.",
        ),
        ModelInfo(
            name="meta.llama3-1-405b-instruct-v1:0",
            provider="bedrock",
            tier=ModelTier.LARGE,
            context_window=128_000,
            max_output_tokens=4_096,
            input_cost_per_mtok=2.40,
            output_cost_per_mtok=2.40,
            supports_tools=True,
            description="Llama 3.1 405B on Bedrock: largest open-weight in-account model.",
        ),
        ModelInfo(
            name="mistral.mistral-large-2407-v1:0",
            provider="bedrock",
            tier=ModelTier.MEDIUM,
            context_window=128_000,
            max_output_tokens=8_192,
            input_cost_per_mtok=2.00,
            output_cost_per_mtok=6.00,
            supports_tools=True,
            description="Mistral Large on Bedrock: capable in-account model.",
        ),
    ]


class ModelRegistry:
    """A searchable catalogue of :class:`ModelInfo`."""

    def __init__(self, models: list[ModelInfo] | None = None) -> None:
        self._models: dict[str, ModelInfo] = {}
        for info in models if models is not None else _default_models():
            self.register(info)

    def register(self, info: ModelInfo) -> None:
        """Add or replace a model entry."""
        self._models[info.name] = info

    def get(self, name: str) -> ModelInfo:
        try:
            return self._models[name]
        except KeyError as exc:
            raise ConfigurationError(
                f"Unknown model {name!r}", context={"known": sorted(self._models)}
            ) from exc

    def has(self, name: str) -> bool:
        return name in self._models

    def all(self) -> list[ModelInfo]:
        return list(self._models.values())

    def by_provider(self, provider: str) -> list[ModelInfo]:
        return [m for m in self._models.values() if m.provider == provider]

    def cost(self, usage: Usage, model: str) -> float:
        """Compute cost for ``usage`` against the named model."""
        return self.get(model).cost(usage)
