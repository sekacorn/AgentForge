"""Tests for the Ollama provider.

All hermetic: every test drives ``OllamaProvider`` through an ``httpx.MockTransport``
so no real Ollama server (and no network) is required. The transport handler inspects
the outgoing request and returns canned Ollama REST responses.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from forge import (
    FinishReason,
    ForgeConfig,
    Message,
    Orchestrator,
    ProviderError,
    ToolSchema,
)
from forge.models.providers.ollama import OllamaProvider

Handler = Callable[[httpx.Request], httpx.Response]


def _provider(handler: Handler) -> OllamaProvider:
    return OllamaProvider(base_url="http://localhost:11434", transport=httpx.MockTransport(handler))


async def test_complete_round_trip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["model"] == "llama3.1:8b"
        return httpx.Response(
            200,
            json={
                "model": "llama3.1:8b",
                "message": {"role": "assistant", "content": "Hello from Ollama"},
                "done": True,
                "prompt_eval_count": 11,
                "eval_count": 7,
            },
        )

    provider = _provider(handler)
    try:
        response = await provider.complete([Message.user("hi")], model="llama3.1:8b")
    finally:
        await provider.aclose()

    assert response.content == "Hello from Ollama"
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 7
    assert response.finish_reason is FinishReason.STOP


async def test_stream_round_trip() -> None:
    lines = [
        json.dumps({"message": {"content": "Hel"}, "done": False}),
        json.dumps({"message": {"content": "lo"}, "done": False}),
        json.dumps({"message": {"content": "!"}, "done": True}),
    ]
    body = ("\n".join(lines) + "\n").encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content=body)

    provider = _provider(handler)
    try:
        chunks = [
            chunk async for chunk in provider.stream([Message.user("hi")], model="mistral:7b")
        ]
    finally:
        await provider.aclose()

    assert chunks == ["Hel", "lo", "!"]
    assert "".join(chunks) == "Hello!"


async def test_tool_call_round_trip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # Tool schemas are forwarded in Ollama's function-tool shape.
        assert body["tools"][0]["function"]["name"] == "calculator"
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "calculator", "arguments": {"expression": "2+2"}}}
                    ],
                },
                "done": True,
                "prompt_eval_count": 20,
                "eval_count": 5,
            },
        )

    tools = [
        ToolSchema(
            name="calculator",
            description="Evaluate arithmetic.",
            parameters={"type": "object", "properties": {"expression": {"type": "string"}}},
        )
    ]
    provider = _provider(handler)
    try:
        response = await provider.complete(
            [Message.user("compute 2+2")], model="llama3.1:8b", tools=tools
        )
    finally:
        await provider.aclose()

    assert response.finish_reason is FinishReason.TOOL_CALLS
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "calculator"
    assert response.tool_calls[0].arguments == {"expression": "2+2"}


async def test_connection_refused_raises_helpful_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    provider = _provider(handler)
    try:
        with pytest.raises(ProviderError) as excinfo:
            await provider.complete([Message.user("hi")], model="llama3.1:8b")
    finally:
        await provider.aclose()

    assert "ollama serve" in str(excinfo.value)


async def test_auto_selects_ollama_when_base_url_set(tmp_path, monkeypatch) -> None:
    # No cloud keys present; OLLAMA_BASE_URL explicitly set -> Ollama is selected.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")

    config = ForgeConfig()  # fresh config: no API keys loaded into api_keys
    config.compliance.audit_path = str(tmp_path / "audit.jsonl")
    orchestrator = Orchestrator(config)
    try:
        assert "ollama" in orchestrator.providers
        assert orchestrator.config.routing.default_provider == "ollama"
        # The cheap Ollama model is pinned for the planning pass.
        assert orchestrator.config.routing.planner_model == "llama3.2:3b"
    finally:
        await orchestrator.aclose()
