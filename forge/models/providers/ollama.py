"""Ollama (local LLM) model provider.

Talks to a locally running `Ollama <https://ollama.com>`_ server over its REST
API using ``httpx`` directly — no third-party ``ollama`` SDK and no API key. Since
``httpx`` is already a core dependency of Forge, it is imported normally at module
level (unlike the optional Anthropic/OpenAI SDKs, which are lazy-imported).

Ollama runs models on local compute, so every model is free ($0) and works fully
offline / air-gapped. The server defaults to ``http://localhost:11434`` and is
configurable via the constructor or the ``OLLAMA_BASE_URL`` environment variable.

Models must be pulled locally before use, e.g.::

    ollama serve
    ollama pull llama3.1:8b
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from forge.exceptions import ProviderError, ProviderResponseError
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

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

#: Raised verbatim when the server cannot be reached, so the operator knows the fix.
_NOT_RUNNING_HINT = "Ollama is not running. Start it with: ollama serve"


class OllamaProvider(ModelProvider):
    """Calls local models through the Ollama REST API (raw ``httpx``, no SDK)."""

    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
        ).rstrip("/")
        client_kwargs: dict[str, Any] = {"base_url": self._base_url, "timeout": timeout}
        if transport is not None:
            # Primarily a testing seam: pass an ``httpx.MockTransport`` to exercise
            # the provider without a running server.
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)

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
        payload = self._build_payload(messages, model, tools, system, max_tokens, stream=False)
        try:
            response = await self._client.post("/api/chat", json=payload)
        except httpx.ConnectError as exc:
            raise ProviderError(_NOT_RUNNING_HINT, context={"base_url": self._base_url}) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                f"Ollama request failed: {exc}", context={"base_url": self._base_url}
            ) from exc

        self._raise_for_status(response, model)
        return self._from_api_response(response.json(), model)

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        **options: Any,
    ) -> AsyncIterator[str]:
        """Stream text chunks from Ollama's newline-delimited JSON response.

        Ollama emits one JSON object per line; each carries an incremental
        ``message.content`` delta and a ``done`` flag that marks the final object.
        """
        payload = self._build_payload(messages, model, tools, system, max_tokens, stream=True)
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code != 200:
                    await response.aread()
                    self._raise_for_status(response, model)
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    message = obj.get("message") or {}
                    content = message.get("content")
                    if isinstance(content, str) and content:
                        yield content
                    if obj.get("done"):
                        break
        except httpx.ConnectError as exc:
            raise ProviderError(_NOT_RUNNING_HINT, context={"base_url": self._base_url}) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                f"Ollama request failed: {exc}", context={"base_url": self._base_url}
            ) from exc

    async def aclose(self) -> None:
        await self._client.aclose()

    def _build_payload(
        self,
        messages: list[Message],
        model: str,
        tools: list[ToolSchema] | None,
        system: str | None,
        max_tokens: int,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._to_api_messages(messages, system),
            "stream": stream,
            "options": {"num_predict": max_tokens},
        }
        if tools:
            payload["tools"] = [self._to_api_tool(t) for t in tools]
        return payload

    @staticmethod
    def _to_api_tool(tool: ToolSchema) -> dict[str, Any]:
        # Ollama uses the OpenAI-compatible function-tool shape.
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
            return [{"role": "tool", "content": tr.content} for tr in message.tool_results]

        if message.role == Role.ASSISTANT and message.tool_calls:
            return [
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        # Ollama takes arguments as a JSON object, not a string.
                        {"function": {"name": call.name, "arguments": call.arguments}}
                        for call in message.tool_calls
                    ],
                }
            ]

        role = "assistant" if message.role == Role.ASSISTANT else "user"
        return [{"role": role, "content": message.content}]

    def _raise_for_status(self, response: httpx.Response, model: str) -> None:
        """Turn a non-200 Ollama response into a clear :class:`ProviderResponseError`.

        Ollama returns ``{"error": "..."}`` for problems such as a missing model or a
        model that does not support tool calling; the message names the model so the
        cause is obvious.
        """
        if response.status_code == 200:
            return
        try:
            detail = response.json().get("error", response.text)
        except json.JSONDecodeError:
            detail = response.text
        raise ProviderResponseError(
            f"Ollama API error for model {model!r}: {detail}",
            context={"model": model, "status_code": response.status_code},
        )

    @staticmethod
    def _from_api_response(data: dict[str, Any], model: str) -> ModelResponse:
        message = data.get("message") or {}
        content = message.get("content") or ""

        tool_calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            tool_calls.append(ToolCall(name=function.get("name", ""), arguments=arguments))

        usage = Usage(
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
        )
        finish = FinishReason.TOOL_CALLS if tool_calls else FinishReason.STOP
        return ModelResponse(
            model=model,
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=usage,
        )
