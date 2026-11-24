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


# --- output-token parameter: explicit data, identical default --------------


def _old_heuristic(model: str) -> str:
    """The rule this provider used before it became data. Kept only to compare."""
    return "max_completion_tokens" if model.startswith("o") else "max_tokens"


@pytest.mark.parametrize(
    "model",
    [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1",
        "o1",
        "o1-mini",
        "o3",
        "o3-mini",
        "o4-mini",
        "gpt-3.5-turbo",
        "chatgpt-4o-latest",
        "omni-moderation-latest",
        "openchat",
        "O3",
        "",
    ],
)
async def test_default_token_param_is_identical_to_the_old_heuristic(monkeypatch, model):
    _, client = _install_fake_openai(monkeypatch, response=_response(content="ok"))
    from forge.models.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test")
    await provider.complete([Message.user("hi")], model=model, max_tokens=123)

    sent = client.chat.completions.create.call_args.kwargs
    expected = _old_heuristic(model)
    other = "max_tokens" if expected == "max_completion_tokens" else "max_completion_tokens"
    assert sent[expected] == 123
    assert other not in sent
    assert provider.token_param_for(model) == expected


@pytest.mark.parametrize("param", ["max_tokens", "max_completion_tokens"])
@pytest.mark.parametrize("model", ["o3", "gpt-4o", "openchat"])
async def test_an_explicit_token_param_wins_for_every_model(monkeypatch, param, model):
    _, client = _install_fake_openai(monkeypatch, response=_response(content="ok"))
    from forge.models.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test", token_param=param)
    await provider.complete([Message.user("hi")], model=model, max_tokens=77)

    sent = client.chat.completions.create.call_args.kwargs
    assert sent[param] == 77
    assert len({"max_tokens", "max_completion_tokens"} & set(sent)) == 1


async def test_empty_prefixes_never_choose_max_completion_tokens(monkeypatch):
    # What a non-OpenAI endpoint wants: a model merely named "o..." is not a
    # reasoning model there.
    _, client = _install_fake_openai(monkeypatch, response=_response(content="ok"))
    from forge.models.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test", completion_token_model_prefixes=())
    await provider.complete([Message.user("hi")], model="openchat", max_tokens=50)

    sent = client.chat.completions.create.call_args.kwargs
    assert sent["max_tokens"] == 50
    assert "max_completion_tokens" not in sent


def test_an_unknown_token_param_is_refused(monkeypatch):
    _install_fake_openai(monkeypatch)
    from forge.models.providers.openai import OpenAIProvider

    with pytest.raises(ValueError, match="token_param must be one of"):
        OpenAIProvider(api_key="sk-test", token_param="max_output_tokens")


def test_the_default_prefixes_are_the_documented_data():
    from forge.models.providers import openai as module

    assert module.DEFAULT_COMPLETION_TOKEN_MODEL_PREFIXES == ("o",)
    assert module.TOKEN_PARAMS == ("max_tokens", "max_completion_tokens")
