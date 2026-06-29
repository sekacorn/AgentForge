"""OpenAI (GPT) model provider.

Uses the official ``openai`` Python SDK (the ``AsyncOpenAI`` client). The SDK is
an *optional* dependency: it is imported lazily inside ``__init__`` so the core
library and the offline ``EchoProvider`` work without it. Install with::

    pip install "agentforge-oss[openai]"

This provider targets the Chat Completions API. Forge ``Message`` types are
mapped to OpenAI's message format, ``ToolSchema`` is mapped to OpenAI function
calling, and the response (text, tool calls, token usage) is mapped back to
Forge's provider-agnostic types.
"""

from __future__ import annotations

import json
from typing import Any

from forge.exceptions import (
    ProviderAuthError,
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

_FINISH_REASON_MAP = {
    "stop": FinishReason.STOP,
    "tool_calls": FinishReason.TOOL_CALLS,
    "function_call": FinishReason.TOOL_CALLS,
    "length": FinishReason.MAX_TOKENS,
    "content_filter": FinishReason.REFUSAL,
}


class OpenAIProvider(ModelProvider):
    """Calls OpenAI GPT models through the official OpenAI SDK."""

    name = "openai"

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None) -> None:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "The 'openai' package is required for OpenAIProvider. "
                "Install it with: pip install 'agentforge-oss[openai]'"
            ) from exc

        self._openai = openai
        client_kwargs: dict[str, Any] = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url
        try:
            self._client = openai.AsyncOpenAI(**client_kwargs)
        except Exception as exc:  # e.g. missing key when none in env
            raise ProviderAuthError(
                "Failed to initialize the OpenAI client (missing API key?)"
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
        request: dict[str, Any] = {
            "model": model,
            "messages": self._to_api_messages(messages, system),
        }
        # The o-series reasoning models use ``max_completion_tokens`` and reject
        # the legacy ``max_tokens`` parameter.
        token_param = "max_completion_tokens" if model.startswith("o") else "max_tokens"
        request[token_param] = max_tokens
        if tools:
            request["tools"] = [self._to_api_tool(t) for t in tools]
        for key in ("tool_choice", "temperature", "response_format"):
            if key in options and options[key] is not None:
                request[key] = options[key]

        try:
            response = await self._client.chat.completions.create(**request)
        except self._openai.AuthenticationError as exc:
            raise ProviderAuthError(
                "OpenAI authentication failed", context={"model": model}
            ) from exc
        except self._openai.RateLimitError as exc:
            raise ProviderRateLimitError("OpenAI rate limit hit", context={"model": model}) from exc
        except self._openai.APIError as exc:
            raise ProviderResponseError(
                f"OpenAI API error: {exc}", context={"model": model}
            ) from exc

        return self._from_api_response(response, model)

    async def aclose(self) -> None:
        await self._client.close()

    @staticmethod
    def _to_api_tool(tool: ToolSchema) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    @classmethod
    def _to_api_messages(cls, messages: list[Message], system: str | None) -> list[dict[str, Any]]:
        # OpenAI takes the system prompt as the first message in the list.
        system_from_msgs, convo = split_system(messages)
        merged_system = "\n\n".join(s for s in (system, system_from_msgs) if s) or None

        api_messages: list[dict[str, Any]] = []
        if merged_system:
            api_messages.append({"role": "system", "content": merged_system})
        for message in convo:
            api_messages.extend(cls._convert_message(message))
        return api_messages

    @staticmethod
    def _convert_message(message: Message) -> list[dict[str, Any]]:
        if message.role == Role.TOOL:
            return [
                {"role": "tool", "tool_call_id": tr.tool_call_id, "content": tr.content}
                for tr in message.tool_results
            ]

        if message.role == Role.ASSISTANT and message.tool_calls:
            return [
                {
                    "role": "assistant",
                    "content": message.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in message.tool_calls
                    ],
                }
            ]

        role = "assistant" if message.role == Role.ASSISTANT else "user"
        return [{"role": role, "content": message.content}]

    @staticmethod
    def _from_api_response(response: Any, model: str) -> ModelResponse:
        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        for tc in getattr(message, "tool_calls", None) or []:
            raw_args = tc.function.arguments
            try:
                arguments: dict[str, Any] = json.loads(raw_args) if raw_args else {}
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=arguments))

        raw_usage = response.usage
        usage = Usage(
            input_tokens=getattr(raw_usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(raw_usage, "completion_tokens", 0) or 0,
        )

        finish = _FINISH_REASON_MAP.get(
            getattr(choice, "finish_reason", "") or "", FinishReason.STOP
        )
        return ModelResponse(
            model=model,
            content=message.content or "",
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=usage,
        )
