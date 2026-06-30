"""Amazon Bedrock model provider (unified Converse API).

Runs models *inside the customer's own AWS account/region* — which is what
GovCloud and enterprise AWS shops need instead of calling Anthropic or OpenAI
directly. It uses Bedrock's modern ``converse`` / ``converse_stream`` API, which
is identical across Claude, Llama, Titan, and Mistral models, so there is no
per-model payload branching.

``boto3`` is a large *optional* dependency, imported lazily so the core library
works without it. Install with::

    pip install "agentforge-oss[bedrock]"

Authentication uses boto3's **standard credential chain** (environment variables,
``~/.aws/credentials``, an IAM role, or SSO) — Forge does not implement custom
credential handling. ``region_name`` and ``profile_name`` may be passed for
explicit control. Every (synchronous) boto3 call is wrapped in
``asyncio.to_thread`` so it never blocks the event loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from forge.exceptions import ProviderError
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

_BEDROCK_IMPORT_HINT = (
    "The 'boto3' package is required for BedrockProvider. "
    "Install it with: pip install 'agentforge-oss[bedrock]'"
)
_NO_CREDENTIALS = (
    "AWS credentials not found. Configure via `aws configure`, environment "
    "variables, or an IAM role."
)
_ACCESS_DENIED = (
    "AWS credentials lack Bedrock access. Check IAM permissions for bedrock:InvokeModel."
)
_MODEL_NOT_ENABLED = (
    "Model not enabled in this AWS account/region. Enable it in the Bedrock console "
    "under Model access."
)

_STOP_REASON_MAP = {
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "tool_use": FinishReason.TOOL_CALLS,
    "max_tokens": FinishReason.MAX_TOKENS,
    "guardrail_intervened": FinishReason.REFUSAL,
    "content_filtered": FinishReason.REFUSAL,
}


class BedrockProvider(ModelProvider):
    """Calls Bedrock models through the unified Converse API via boto3."""

    name = "bedrock"

    def __init__(
        self,
        *,
        region_name: str | None = None,
        profile_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is not None:
            # Test seam: inject a stand-in bedrock-runtime client.
            self._client = client
            return
        try:
            import boto3
        except ImportError as exc:
            raise ImportError(_BEDROCK_IMPORT_HINT) from exc
        session = boto3.Session(region_name=region_name, profile_name=profile_name)
        self._client = session.client("bedrock-runtime")

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
        request = self._build_request(messages, model, tools, system, max_tokens)
        from botocore.exceptions import ClientError, NoCredentialsError

        try:
            # boto3 is synchronous; run it off the event loop.
            response = await asyncio.to_thread(self._client.converse, **request)
        except NoCredentialsError as exc:
            raise ProviderError(_NO_CREDENTIALS, context={"model": model}) from exc
        except ClientError as exc:
            raise self._map_client_error(exc, model) from exc

        return self._from_response(response, model)

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
        """Stream text deltas from Bedrock's ``converse_stream`` event stream.

        Each ``contentBlockDelta`` event carries an incremental text chunk; the
        terminal ``messageStop`` event ends iteration. Every blocking step (the
        initial call and each ``next()`` on the event stream) runs in a thread.
        """
        request = self._build_request(messages, model, tools, system, max_tokens)
        from botocore.exceptions import ClientError, NoCredentialsError

        try:
            response = await asyncio.to_thread(self._client.converse_stream, **request)
            stream = iter(response["stream"])
            while True:
                event = await asyncio.to_thread(_next_event, stream)
                if event is None or "messageStop" in event:
                    break
                delta = event.get("contentBlockDelta", {}).get("delta", {})
                text = delta.get("text")
                if isinstance(text, str) and text:
                    yield text
        except NoCredentialsError as exc:
            raise ProviderError(_NO_CREDENTIALS, context={"model": model}) from exc
        except ClientError as exc:
            raise self._map_client_error(exc, model) from exc

    # ------------------------------------------------------------------ #
    # Error mapping
    # ------------------------------------------------------------------ #
    def _map_client_error(self, exc: Any, model: str) -> ProviderError:
        code = ""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = response.get("Error", {}).get("Code", "")
        if code == "AccessDeniedException":
            return ProviderError(_ACCESS_DENIED, context={"model": model, "code": code})
        if code in ("ResourceNotFoundException", "ValidationException"):
            return ProviderError(_MODEL_NOT_ENABLED, context={"model": model, "code": code})
        return ProviderError(f"Bedrock API error: {exc}", context={"model": model, "code": code})

    # ------------------------------------------------------------------ #
    # Translation: Forge types -> Converse wire format
    # ------------------------------------------------------------------ #
    def _build_request(
        self,
        messages: list[Message],
        model: str,
        tools: list[ToolSchema] | None,
        system: str | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        system_from_msgs, convo = split_system(messages)
        merged_system = "\n\n".join(s for s in (system, system_from_msgs) if s) or None

        request: dict[str, Any] = {
            "modelId": model,
            "messages": [self._to_api_message(m) for m in convo],
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if merged_system:
            request["system"] = [{"text": merged_system}]
        if tools:
            request["toolConfig"] = {"tools": [self._to_api_tool(t) for t in tools]}
        return request

    @staticmethod
    def _to_api_tool(tool: ToolSchema) -> dict[str, Any]:
        return {
            "toolSpec": {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": {"json": tool.parameters},
            }
        }

    @staticmethod
    def _to_api_message(message: Message) -> dict[str, Any]:
        if message.role == Role.TOOL:
            return {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": tr.tool_call_id,
                            "content": [{"text": tr.content}],
                            "status": "error" if tr.is_error else "success",
                        }
                    }
                    for tr in message.tool_results
                ],
            }

        if message.role == Role.ASSISTANT and message.tool_calls:
            content: list[dict[str, Any]] = []
            if message.content:
                content.append({"text": message.content})
            for call in message.tool_calls:
                content.append(
                    {"toolUse": {"toolUseId": call.id, "name": call.name, "input": call.arguments}}
                )
            return {"role": "assistant", "content": content}

        role = "assistant" if message.role == Role.ASSISTANT else "user"
        return {"role": role, "content": [{"text": message.content}]}

    # ------------------------------------------------------------------ #
    # Translation: Converse response -> Forge types
    # ------------------------------------------------------------------ #
    @staticmethod
    def _from_response(response: dict[str, Any], model: str) -> ModelResponse:
        message = response.get("output", {}).get("message", {})
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in message.get("content", []):
            if "text" in block:
                text_parts.append(block["text"])
            elif "toolUse" in block:
                use = block["toolUse"]
                tool_calls.append(
                    ToolCall(
                        id=use.get("toolUseId", ""),
                        name=use.get("name", ""),
                        arguments=dict(use.get("input") or {}),
                    )
                )

        raw_usage = response.get("usage", {})
        usage = Usage(
            input_tokens=int(raw_usage.get("inputTokens") or 0),
            output_tokens=int(raw_usage.get("outputTokens") or 0),
        )
        finish = _STOP_REASON_MAP.get(response.get("stopReason", ""), FinishReason.STOP)
        return ModelResponse(
            model=model,
            content="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=usage,
        )


def _next_event(stream: Any) -> Any:
    """Pull the next Bedrock stream event, or ``None`` at the end.

    A module-level helper so it can be handed to ``asyncio.to_thread`` cleanly.
    """
    return next(stream, None)
