"""Core data types shared across Forge.

These are deliberately provider-agnostic. Model providers translate between
their wire formats and these types, so the rest of the system (agents,
orchestration, memory, observability) only ever deals with this vocabulary.

Pydantic v2 models are used for everything that crosses a boundary or gets
serialized (messages, usage, audit payloads): validation and clean JSON
serialization matter more than micro-optimizations for an enterprise tool.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    """Timezone-aware UTC now. Used everywhere so timestamps are consistent."""
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """Generate a short, prefixed, collision-resistant identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


class Role(enum.StrEnum):
    """The author of a message in a conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(enum.StrEnum):
    """Why a model stopped generating."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    ERROR = "error"


class ToolSchema(BaseModel):
    """A provider-agnostic description of a callable tool.

    ``parameters`` is a JSON Schema object describing the tool's input. Providers
    map this onto their own tool-definition format (e.g. Anthropic's
    ``input_schema``).
    """

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolCall(BaseModel):
    """A request, emitted by a model, to invoke a tool with arguments."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: new_id("call"))
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """The outcome of executing a :class:`ToolCall`."""

    tool_call_id: str
    name: str
    content: str
    is_error: bool = False


class Message(BaseModel):
    """A single turn in a conversation.

    A ``Message`` may carry plain ``content``, a set of ``tool_calls`` requested
    by the assistant, or ``tool_results`` produced by executing tools. Keeping
    all three on one model keeps the agent loop straightforward.
    """

    role: Role
    content: str = ""
    name: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # -- Ergonomic constructors --------------------------------------------- #
    @classmethod
    def system(cls, content: str) -> Message:
        return cls(role=Role.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> Message:
        return cls(role=Role.USER, content=content)

    @classmethod
    def assistant(cls, content: str = "", tool_calls: list[ToolCall] | None = None) -> Message:
        return cls(role=Role.ASSISTANT, content=content, tool_calls=tool_calls or [])

    @classmethod
    def tool(cls, results: list[ToolResult]) -> Message:
        return cls(role=Role.TOOL, tool_results=results)


class Usage(BaseModel):
    """Token and cost accounting for one or more model calls.

    ``Usage`` objects are additive so per-call usage can be accumulated into
    per-agent and per-run totals with ``sum(...)``.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        if not isinstance(other, Usage):  # pragma: no cover - defensive
            return NotImplemented
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=round(self.cost_usd + other.cost_usd, 8),
        )

    def __radd__(self, other: Any) -> Usage:
        # ``sum([...])`` starts its accumulator at int(0); treat 0 as identity so
        # per-call Usage objects can be summed directly.
        if other == 0:
            return self
        return self.__add__(other)


class ModelResponse(BaseModel):
    """A normalized response from a model provider."""

    model: str
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: FinishReason = FinishReason.STOP
    usage: Usage = Field(default_factory=Usage)
    raw: dict[str, Any] | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)
