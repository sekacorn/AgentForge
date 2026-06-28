"""Hermetic tests for the OpenAI provider.

The OpenAI SDK is never imported for real: a fake ``openai`` module is injected
into ``sys.modules`` so the provider is exercised without the dependency
installed and without any network calls.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge.types import FinishReason, Message, ToolSchema


def _install_fake_openai(monkeypatch, *, response=None, side_effect=None):
    """Inject a fake ``openai`` module exposing AsyncOpenAI and the error types."""
    module = types.ModuleType("openai")

    class AuthenticationError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class APIError(Exception):
        pass

    module.AuthenticationError = AuthenticationError  # type: ignore[attr-defined]
    module.RateLimitError = RateLimitError  # type: ignore[attr-defined]
    module.APIError = APIError  # type: ignore[attr-defined]

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response, side_effect=side_effect)
    client.close = AsyncMock()
    module.AsyncOpenAI = MagicMock(return_value=client)  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "openai", module)
    return module, client


def _response(*, content=None, tool_calls=None, finish="stop", prompt=10, completion=5):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish)
    usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
    return SimpleNamespace(choices=[choice], usage=usage)


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


async def test_basic_completion_round_trip(monkeypatch):
    _install_fake_openai(
        monkeypatch, response=_response(content="Hello there", prompt=12, completion=7)
    )
    from forge.models.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test")
    result = await provider.complete([Message.user("hi")], model="gpt-4o-mini")

    assert result.content == "Hello there"
    assert result.finish_reason is FinishReason.STOP
    assert result.tool_calls == []
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 7


async def test_tool_call_round_trip(monkeypatch):
    call = _tool_call("call_abc", "calculator", '{"expression": "2 + 2"}')
    _install_fake_openai(
        monkeypatch,
        response=_response(content=None, tool_calls=[call], finish="tool_calls"),
    )
    from forge.models.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test")
    tools = [
        ToolSchema(
            name="calculator",
            description="Evaluate arithmetic",
            parameters={"type": "object", "properties": {}},
        )
    ]
    result = await provider.complete(
        [Message.user("compute 2+2")], model="gpt-4o-mini", tools=tools
    )

    assert result.has_tool_calls
    assert len(result.tool_calls) == 1
    returned = result.tool_calls[0]
    assert returned.id == "call_abc"
    assert returned.name == "calculator"
    assert returned.arguments == {"expression": "2 + 2"}
    assert result.finish_reason is FinishReason.TOOL_CALLS


async def test_token_counting_populated(monkeypatch):
    _install_fake_openai(monkeypatch, response=_response(content="ok", prompt=123, completion=45))
    from forge.models.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test")
    result = await provider.complete([Message.user("hi")], model="gpt-4o")

    assert result.usage.input_tokens == 123
    assert result.usage.output_tokens == 45
    assert result.usage.total_tokens == 168


def test_missing_sdk_raises_helpful_importerror(monkeypatch):
    # ``None`` in sys.modules makes ``import openai`` raise ImportError.
    monkeypatch.setitem(sys.modules, "openai", None)
    from forge.models.providers.openai import OpenAIProvider

    with pytest.raises(ImportError) as exc_info:
        OpenAIProvider(api_key="sk-test")
    message = str(exc_info.value)
    assert "openai" in message.lower()
    assert "agentforge-oss[openai]" in message
