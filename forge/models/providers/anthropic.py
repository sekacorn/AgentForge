"""Anthropic (Claude) model provider.

Uses the official ``anthropic`` Python SDK (the ``AsyncAnthropic`` client). The
SDK is an *optional* dependency: it is imported lazily so the core library and
the offline ``EchoProvider`` work without it. Install with::

    pip install "agentforge[anthropic]"

This provider targets the Messages API and the current Claude family. It follows
the modern request surface for Opus 4.x / Sonnet 4.6 / Fable 5: no sampling
parameters, and adaptive thinking only (passed through via ``options`` when a
caller opts in). Tool definitions, tool calls, and tool results are translated
to and from Forge's provider-agnostic types.
"""

from __future__ import annotations

from typing import Any

from forge.exceptions import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
)
from forge.models.base import ModelProvider, split_system
from forge.types import (
    FinishReason,
    Message,
    ModelResponse,
    Role,
    ToolCall,
    ToolSchema,
    Usage,
)

_STOP_REASON_MAP = {
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "tool_use": FinishReason.TOOL_CALLS,
    "max_tokens": FinishReason.MAX_TOKENS,
    "refusal": FinishReason.REFUSAL,
}


class AnthropicProvider(ModelProvider):
    """Calls Claude models through the official Anthropic SDK."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None, *, anthropic_version: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ProviderError(
                "The 'anthropic' package is required for AnthropicProvider. "
                "Install it with: pip install 'agentforge[anthropic]'"
            ) from exc

        self._anthropic = anthropic
        client_kwargs: dict[str, Any] = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if anthropic_version:
            client_kwargs["default_headers"] = {"anthropic-version": anthropic_version}
        try:
            self._client = anthropic.AsyncAnthropic(**client_kwargs)
        except Exception as exc:  # e.g. missing key when none in env
            raise ProviderAuthError(
                "Failed to initialize the Anthropic client (missing API key?)"
            ) from exc

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        **options: object,
    ) -> ModelResponse:
        system_from_msgs, convo = split_system(messages)
        merged_system = "\n\n".join(s for s in (system, system_from_msgs) if s) or None

        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [self._to_api_message(m) for m in convo],
        }
        if merged_system:
            request["system"] = merged_system
        if tools:
            request["tools"] = [self._to_api_tool(t) for t in tools]
        # Pass-through for advanced options (e.g. thinking={"type": "adaptive"}).
        for key in ("thinking", "tool_choice", "output_config"):
            if key in options and options[key] is not None:
                request[key] = options[key]

        try:
            response = await self._client.messages.create(**request)
        except self._anthropic.AuthenticationError as exc:
            raise ProviderAuthError("Anthropic authentication failed", context={"model": model}) from exc
        except self._anthropic.RateLimitError as exc:
            raise ProviderRateLimitError("Anthropic rate limit hit", context={"model": model}) from exc
        except self._anthropic.APIError as exc:
            raise ProviderResponseError(
                f"Anthropic API error: {exc}", context={"model": model}
            ) from exc

        return self._from_api_response(response, model)

    async def aclose(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------------ #
    # Translation: Forge types -> Anthropic wire format
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_api_tool(tool: ToolSchema) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
        }

    @staticmethod
    def _to_api_message(message: Message) -> dict[str, Any]:
        if message.role == Role.TOOL:
            # Tool results are delivered back to Claude as a user message of
            # tool_result blocks.
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tr.tool_call_id,
                        "content": tr.content,
                        "is_error": tr.is_error,
                    }
                    for tr in message.tool_results
                ],
            }

        if message.role == Role.ASSISTANT and message.tool_calls:
            content: list[dict[str, Any]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            return {"role": "assistant", "content": content}

        role = "assistant" if message.role == Role.ASSISTANT else "user"
        return {"role": role, "content": message.content}

    # ------------------------------------------------------------------ #
    # Translation: Anthropic response -> Forge types
    # ------------------------------------------------------------------ #
    @staticmethod
    def _from_api_response(response: Any, model: str) -> ModelResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(block.text)
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {}))
                )

        raw_usage = response.usage
        usage = Usage(
            input_tokens=getattr(raw_usage, "input_tokens", 0) or 0,
            output_tokens=getattr(raw_usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(raw_usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(raw_usage, "cache_creation_input_tokens", 0) or 0,
        )

        finish = _STOP_REASON_MAP.get(getattr(response, "stop_reason", "") or "", FinishReason.STOP)
        return ModelResponse(
            model=model,
            content="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=usage,
        )
