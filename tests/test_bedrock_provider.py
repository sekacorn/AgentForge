"""Tests for the Amazon Bedrock provider.

Fully hermetic: a hand-rolled fake stands in for the ``bedrock-runtime`` client,
so no real AWS calls or credentials are needed. The whole module skips cleanly
when the optional ``boto3`` dependency is absent.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("boto3")

from botocore.exceptions import ClientError, NoCredentialsError  # noqa: E402

from forge import FinishReason, Message, ProviderError, ToolSchema  # noqa: E402
from forge.models.providers.bedrock import BedrockProvider  # noqa: E402

_MODEL = "anthropic.claude-3-5-sonnet-20241022-v2:0"


class _FakeBedrockClient:
    """Minimal stand-in for a boto3 bedrock-runtime client."""

    def __init__(self, *, converse_response=None, stream_events=None, raises=None):
        self._converse_response = converse_response
        self._stream_events = stream_events or []
        self._raises = raises
        self.converse_thread: threading.Thread | None = None
        self.last_request: dict | None = None

    def converse(self, **kwargs):
        self.converse_thread = threading.current_thread()
        self.last_request = kwargs
        if self._raises is not None:
            raise self._raises
        return self._converse_response

    def converse_stream(self, **kwargs):
        self.last_request = kwargs
        if self._raises is not None:
            raise self._raises
        return {"stream": list(self._stream_events)}


async def test_complete_round_trip() -> None:
    fake = _FakeBedrockClient(
        converse_response={
            "output": {
                "message": {"role": "assistant", "content": [{"text": "Hello from Bedrock"}]}
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 12, "outputTokens": 8, "totalTokens": 20},
        }
    )
    provider = BedrockProvider(client=fake)
    response = await provider.complete([Message.user("hi")], model=_MODEL)

    assert response.content == "Hello from Bedrock"
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 8
    assert response.finish_reason is FinishReason.STOP
    # The request used the Converse shape.
    assert fake.last_request["modelId"] == _MODEL
    assert fake.last_request["messages"][0]["content"][0]["text"] == "hi"
    assert fake.last_request["inferenceConfig"]["maxTokens"] == 4096


async def test_stream_round_trip() -> None:
    fake = _FakeBedrockClient(
        stream_events=[
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"delta": {"text": "Hel"}, "contentBlockIndex": 0}},
            {"contentBlockDelta": {"delta": {"text": "lo"}, "contentBlockIndex": 0}},
            {"contentBlockDelta": {"delta": {"text": "!"}, "contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 3}}},
        ]
    )
    provider = BedrockProvider(client=fake)
    chunks = [chunk async for chunk in provider.stream([Message.user("hi")], model=_MODEL)]

    assert chunks == ["Hel", "lo", "!"]
    assert "".join(chunks) == "Hello!"


async def test_tool_call_round_trip() -> None:
    fake = _FakeBedrockClient(
        converse_response={
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tool-1",
                                "name": "calculator",
                                "input": {"expression": "2+2"},
                            }
                        }
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 20, "outputTokens": 5},
        }
    )
    tools = [
        ToolSchema(
            name="calculator",
            description="Evaluate arithmetic.",
            parameters={"type": "object", "properties": {"expression": {"type": "string"}}},
        )
    ]
    provider = BedrockProvider(client=fake)
    response = await provider.complete([Message.user("compute 2+2")], model=_MODEL, tools=tools)

    assert response.finish_reason is FinishReason.TOOL_CALLS
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "calculator"
    assert response.tool_calls[0].arguments == {"expression": "2+2"}
    # The tool schema was forwarded in Converse's toolConfig shape.
    assert fake.last_request["toolConfig"]["tools"][0]["toolSpec"]["name"] == "calculator"


async def test_no_credentials_error() -> None:
    fake = _FakeBedrockClient(raises=NoCredentialsError())
    provider = BedrockProvider(client=fake)
    with pytest.raises(ProviderError) as excinfo:
        await provider.complete([Message.user("hi")], model=_MODEL)
    assert "AWS credentials not found" in str(excinfo.value)


async def test_access_denied_error() -> None:
    err = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "Converse")
    fake = _FakeBedrockClient(raises=err)
    provider = BedrockProvider(client=fake)
    with pytest.raises(ProviderError) as excinfo:
        await provider.complete([Message.user("hi")], model=_MODEL)
    assert "lack Bedrock access" in str(excinfo.value)


async def test_boto3_calls_run_off_the_event_loop() -> None:
    # Structural proof that the synchronous boto3 call went through asyncio.to_thread:
    # it must execute on a worker thread, never the main (event-loop) thread.
    fake = _FakeBedrockClient(
        converse_response={
            "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1, "outputTokens": 1},
        }
    )
    provider = BedrockProvider(client=fake)
    await provider.complete([Message.user("hi")], model=_MODEL)

    assert fake.converse_thread is not None
    assert fake.converse_thread is not threading.main_thread()
