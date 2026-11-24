"""The generic OpenAI-compatible provider, against a fake in-process server.

Nothing here contacts a vendor, uses an API key, or opens a socket. The "server"
is an ``httpx.MockTransport`` handler handed to the real SDK client, so the
request that would have gone over the wire is inspected exactly as a server
would see it: path, Authorization header and JSON body.

Passing these tests says the provider emits and parses the Chat Completions
shape. It says nothing about any vendor's real API.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("openai", reason="the openai SDK extra is needed for the fake-server tests")

from forge.exceptions import (  # noqa: E402
    ConfigurationError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderResponseError,
)
from forge.models.providers.openai_compatible import (  # noqa: E402
    LOCAL_PLACEHOLDER_KEY,
    OpenAICompatibleProvider,
)
from forge.types import FinishReason, Message, ToolSchema  # noqa: E402

SECRET = "sk-fake-test-key-9f2c7a1e-do-not-log"
BASE_URL = "http://localhost:8000/v1"


class FakeServer:
    """A minimal OpenAI-compatible Chat Completions server, in process."""

    def __init__(self, *, reply: dict[str, Any] | None = None, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []
        self.reply = reply
        self.status = status

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.status != 200:
            # Echo the Authorization header back, as a careless server might.
            return httpx.Response(
                self.status,
                json={
                    "error": {
                        "message": f"bad request with {request.headers.get('authorization')}",
                        "type": "error",
                    }
                },
            )
        problems = self._validate(body)
        if problems:
            return httpx.Response(400, json={"error": {"message": "; ".join(problems)}})
        return httpx.Response(200, json=self.reply or text_reply("hello from the fake server"))

    @staticmethod
    def _validate(body: dict[str, Any]) -> list[str]:
        """Check the request the way a strict server would."""
        problems = []
        if not isinstance(body.get("model"), str) or not body["model"]:
            problems.append("model must be a non-empty string")
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            problems.append("messages must be a non-empty list")
        else:
            for message in messages:
                if message.get("role") not in {"system", "user", "assistant", "tool"}:
                    problems.append(f"unknown role {message.get('role')!r}")
                if message.get("role") == "tool" and "tool_call_id" not in message:
                    problems.append("a tool message needs tool_call_id")
        for tool in body.get("tools", []):
            function = tool.get("function", {})
            if tool.get("type") != "function" or not function.get("name"):
                problems.append("each tool must be a named function")
            if not isinstance(function.get("parameters"), dict):
                problems.append("tool parameters must be a JSON Schema object")
        return problems

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def text_reply(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "fake-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def tool_reply() -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "fake-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "calculator",
                                "arguments": '{"expression": "2 + 2"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 9, "total_tokens": 29},
    }


CALCULATOR = ToolSchema(
    name="calculator",
    description="Evaluate an arithmetic expression.",
    parameters={
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
    },
)


def _provider(server: FakeServer, **kwargs: Any) -> OpenAICompatibleProvider:
    kwargs.setdefault("base_url", BASE_URL)
    kwargs.setdefault("api_key", SECRET)
    return OpenAICompatibleProvider(http_client=server.client(), **kwargs)


# --- request shape ----------------------------------------------------------


async def test_a_completion_reaches_the_configured_endpoint_in_the_documented_shape() -> None:
    server = FakeServer()
    provider = _provider(server)

    result = await provider.complete(
        [Message.user("hi")], model="some-model", system="be brief", max_tokens=64
    )

    request, body = server.requests[0], server.bodies[0]
    assert request.method == "POST"
    assert str(request.url) == "http://localhost:8000/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert body["model"] == "some-model"
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    assert body["max_tokens"] == 64
    assert "max_completion_tokens" not in body
    assert result.content == "hello from the fake server"
    assert result.finish_reason is FinishReason.STOP
    assert (result.usage.input_tokens, result.usage.output_tokens) == (11, 7)


async def test_a_model_named_like_a_reasoning_model_still_gets_max_tokens() -> None:
    # Behind a compatible endpoint a name starting with "o" means nothing.
    server = FakeServer()
    provider = _provider(server)

    await provider.complete([Message.user("hi")], model="openchat-3.5", max_tokens=32)

    assert server.bodies[0]["max_tokens"] == 32
    assert "max_completion_tokens" not in server.bodies[0]


async def test_a_preset_or_argument_can_choose_the_other_token_parameter() -> None:
    server = FakeServer()
    provider = _provider(server, token_param="max_completion_tokens")

    await provider.complete([Message.user("hi")], model="m", max_tokens=32)

    assert server.bodies[0]["max_completion_tokens"] == 32
    assert "max_tokens" not in server.bodies[0]


# --- tool calling -----------------------------------------------------------


async def test_tools_are_sent_as_functions_and_a_tool_call_is_parsed() -> None:
    server = FakeServer(reply=tool_reply())
    provider = _provider(server)

    result = await provider.complete(
        [Message.user("what is 2 + 2?")], model="m", tools=[CALCULATOR]
    )

    assert server.bodies[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "Evaluate an arithmetic expression.",
                "parameters": CALCULATOR.parameters,
            },
        }
    ]
    assert result.finish_reason is FinishReason.TOOL_CALLS
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert (call.id, call.name, call.arguments) == ("call_1", "calculator", {"expression": "2 + 2"})


async def test_a_tool_result_round_trip_is_accepted_by_a_strict_server() -> None:
    # First turn: the model asks for a tool. Second turn: the result goes back
    # with the tool_call_id the server validates.
    server = FakeServer(reply=tool_reply())
    provider = _provider(server)
    first = await provider.complete([Message.user("2 + 2?")], model="m", tools=[CALCULATOR])

    server.reply = text_reply("4")
    history = [
        Message.user("2 + 2?"),
        Message.assistant("", tool_calls=first.tool_calls),
        Message.tool([_tool_result(first.tool_calls[0].id, "4")]),
    ]
    second = await provider.complete(history, model="m", tools=[CALCULATOR])

    sent = server.bodies[1]["messages"]
    assert sent[1]["role"] == "assistant"
    assert sent[1]["tool_calls"][0]["function"]["arguments"] == '{"expression": "2 + 2"}'
    assert sent[2] == {"role": "tool", "tool_call_id": "call_1", "content": "4"}
    assert second.content == "4"


def _tool_result(call_id: str, content: str) -> Any:
    from forge.types import ToolResult

    return ToolResult(tool_call_id=call_id, name="calculator", content=content)


# --- keys never leak --------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, ProviderAuthError), (429, ProviderRateLimitError), (500, ProviderResponseError)],
)
async def test_the_api_key_never_appears_in_an_error(
    status: int, error: type[Exception], caplog: pytest.LogCaptureFixture
) -> None:
    # The fake server echoes the Authorization header in its error body, which
    # is the worst case: the upstream message itself contains the key.
    server = FakeServer(status=status)
    provider = OpenAICompatibleProvider(
        base_url=BASE_URL, api_key=SECRET, http_client=server.client()
    )
    provider._client.max_retries = 0

    with caplog.at_level(logging.DEBUG), pytest.raises(error) as raised:
        await provider.complete([Message.user("hi")], model="m")

    rendered = "\n".join(
        [
            str(raised.value),
            repr(raised.value),
            json.dumps(raised.value.context, default=str),  # type: ignore[attr-defined]
            repr(raised.value.__cause__),
            repr(raised.value.__context__ if raised.value.__suppress_context__ is False else None),
            caplog.text,
        ]
    )
    assert SECRET not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.context["status_code"] == status  # type: ignore[attr-defined]
    assert raised.value.context["endpoint"] == "http://localhost:8000"  # type: ignore[attr-defined]


def test_the_api_key_never_appears_in_the_provider_repr_or_block() -> None:
    server = FakeServer()
    provider = _provider(server)

    rendered = repr(provider) + str(provider) + repr(provider.provider_block)

    assert SECRET not in rendered
    assert "localhost:8000" in repr(provider)


def test_the_key_comes_from_the_named_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_ENDPOINT_KEY", SECRET)
    server = FakeServer()

    provider = OpenAICompatibleProvider(
        base_url="https://llm.example.com/v1",
        api_key_env="MY_ENDPOINT_KEY",
        http_client=server.client(),
    )

    assert provider.api_key_env == "MY_ENDPOINT_KEY"
    assert SECRET not in repr(provider)


def test_a_hosted_endpoint_without_a_key_is_refused_naming_the_variable_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MY_ENDPOINT_KEY", raising=False)

    with pytest.raises(ProviderAuthError) as raised:
        OpenAICompatibleProvider(
            base_url="https://llm.example.com/v1/tenant-path", api_key_env="MY_ENDPOINT_KEY"
        )

    assert raised.value.context["api_key_env"] == "MY_ENDPOINT_KEY"
    # The endpoint is reported by origin only: no path, which may name a tenant.
    assert raised.value.context["endpoint"] == "https://llm.example.com"
    assert "tenant-path" not in str(raised.value)


async def test_a_local_endpoint_needs_no_key() -> None:
    server = FakeServer()
    provider = OpenAICompatibleProvider(base_url=BASE_URL, http_client=server.client())

    await provider.complete([Message.user("hi")], model="m")

    assert server.requests[0].headers["authorization"] == f"Bearer {LOCAL_PLACEHOLDER_KEY}"


# --- construction -----------------------------------------------------------


def test_neither_a_preset_nor_a_base_url_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="needs a base_url or a preset"):
        OpenAICompatibleProvider()


def test_an_unknown_preset_is_refused_with_the_known_ids() -> None:
    with pytest.raises(ConfigurationError, match="Unknown provider preset") as raised:
        OpenAICompatibleProvider(preset="no_such_preset")

    assert "local_vllm" in raised.value.context["known_presets"]


def test_a_non_http_base_url_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="http"):
        OpenAICompatibleProvider(base_url="file:///etc/passwd")


def test_a_local_preset_builds_without_a_key_and_describes_itself() -> None:
    provider = OpenAICompatibleProvider(preset="local_vllm")

    block = provider.provider_block
    assert (block.provider, block.hosting, block.jurisdiction) == (
        "openai_compatible",
        "local",
        "not_applicable",
    )
    assert block.preset == "local_vllm"


def test_a_custom_base_url_has_unknown_jurisdiction_and_derived_hosting() -> None:
    hosted = OpenAICompatibleProvider(base_url="https://llm.example.com/v1", api_key=SECRET)
    local = OpenAICompatibleProvider(base_url="http://192.168.1.20:8000/v1")

    assert (hosted.provider_block.hosting, hosted.provider_block.jurisdiction) == (
        "hosted",
        "unknown",
    )
    assert (local.provider_block.hosting, local.provider_block.jurisdiction) == (
        "local",
        "not_applicable",
    )
    assert hosted.provider_block.preset is None


def test_a_preset_pointed_elsewhere_loses_the_presets_jurisdiction() -> None:
    # A preset describes its vendor's endpoint, not wherever it was repointed.
    provider = OpenAICompatibleProvider(
        preset="local_vllm", base_url="https://llm.example.com/v1", api_key=SECRET
    )

    block = provider.provider_block
    assert (block.hosting, block.jurisdiction, block.preset) == ("hosted", "unknown", None)


# --- naming -----------------------------------------------------------------


def test_the_registry_key_and_module_are_vendor_neutral() -> None:
    from forge.models import providers

    assert OpenAICompatibleProvider.name == "openai_compatible"
    assert OpenAICompatibleProvider.__module__ == "forge.models.providers.openai_compatible"
    assert "OpenAICompatibleProvider" in providers.__all__


def test_no_preset_vendor_name_appears_in_any_module_class_or_cli_name() -> None:
    from forge.models.presets import load_presets

    # Preset ids that are not generic words. "local_*" ids name software an
    # operator runs, and are checked the same way.
    vendor_tokens = set()
    for preset_id in load_presets():
        vendor_tokens.update(
            part for part in preset_id.split("_") if part not in {"local", "openai"}
        )
    assert {"mistral", "deepseek", "groq"} <= vendor_tokens

    root = Path(__file__).resolve().parents[1] / "forge"
    offenders = []
    for path in root.rglob("*.py"):
        stem_parts = set(re.split(r"[_.\-]", path.stem.lower())) | {
            part.lower() for part in path.relative_to(root).parts[:-1]
        }
        if stem_parts & (vendor_tokens - {"ollama"}):
            offenders.append(f"module {path.relative_to(root)}")
        for match in re.finditer(
            r"^\s*(?:class|def)\s+([A-Za-z_][A-Za-z0-9_]*)", path.read_text("utf-8"), re.M
        ):
            name = match.group(1).lower()
            if any(token in name for token in vendor_tokens - {"ollama"}):
                offenders.append(f"{path.relative_to(root)}::{match.group(1)}")
    assert offenders == []
