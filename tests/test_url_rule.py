"""One rule for every configured URL, and default_query kept out of records and logs.

Credentials and fragments are always refused. A query string is refused in a
base URL; parameters an endpoint needs (an ``api-version``, say) go in
``default_query``, whose values are sent but never recorded or logged. Every
refusal is checked for never repeating the URL. No socket is opened.
"""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any

import httpx
import pytest

from forge.config import ForgeConfig
from forge.exceptions import ConfigurationError
from forge.models.endpoints import (
    URL_CREDENTIALS_MESSAGE,
    URL_FRAGMENT_MESSAGE,
    URL_QUERY_MESSAGE,
    url_problem,
    validate_default_query,
)
from forge.types import Message

SECRET = "urlrule-secret-61de-do-not-log"
BAD_URLS = {
    "credentials": (f"https://user:{SECRET}@llm.example.com/v1", URL_CREDENTIALS_MESSAGE),
    "fragment": (f"https://llm.example.com/v1#{SECRET}", URL_FRAGMENT_MESSAGE),
    "query": (f"https://llm.example.com/v1?key={SECRET}", URL_QUERY_MESSAGE),
    "empty query": ("https://llm.example.com/v1?", URL_QUERY_MESSAGE),
}


def _rendered(error: BaseException) -> str:
    return "\n".join(
        [
            str(error),
            repr(error),
            repr(getattr(error, "context", None)),
            "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        ]
    )


def _assert_refused(raised: pytest.ExceptionInfo[ConfigurationError], message: str) -> None:
    assert str(raised.value).startswith(message)
    assert SECRET not in _rendered(raised.value)


# --- the rule -------------------------------------------------------------------


@pytest.mark.parametrize("case", sorted(BAD_URLS))
def test_the_rule_names_the_problem_without_the_url(case: str) -> None:
    url, message = BAD_URLS[case]

    assert url_problem(url) == message
    assert SECRET not in message


def test_a_query_is_allowed_only_when_asked_and_credentials_never_are() -> None:
    assert url_problem("https://llm.example.com/v1?a=b", allow_query=True) is None
    assert url_problem(BAD_URLS["credentials"][0], allow_query=True) == URL_CREDENTIALS_MESSAGE
    assert url_problem(BAD_URLS["fragment"][0], allow_query=True) == URL_FRAGMENT_MESSAGE


@pytest.mark.parametrize(
    "url", ["https://llm.example.com/v1", "http://localhost:11434", "https://h.example/a@b/c"]
)
def test_a_clean_url_passes(url: str) -> None:
    assert url_problem(url) is None


# --- every entry point ------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (
        "OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "OLLAMA_BASE_URL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "FORGE_OPENAI_COMPATIBLE_DEFAULT_QUERY",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize("case", sorted(BAD_URLS))
def test_openai_refuses_a_bad_base_url(case: str, clean_env: pytest.MonkeyPatch) -> None:
    pytest.importorskip("openai")
    from forge.models.providers.openai import OpenAIProvider

    url, message = BAD_URLS[case]
    with pytest.raises(ConfigurationError) as raised:
        OpenAIProvider(api_key="sk-fake", base_url=url)
    _assert_refused(raised, message)


@pytest.mark.parametrize("case", sorted(BAD_URLS))
def test_openai_refuses_a_bad_sdk_environment_url(case: str, clean_env: pytest.MonkeyPatch) -> None:
    pytest.importorskip("openai")
    from forge.models.providers.openai import OpenAIProvider

    url, message = BAD_URLS[case]
    clean_env.setenv("OPENAI_BASE_URL", url)
    with pytest.raises(ConfigurationError) as raised:
        OpenAIProvider(api_key="sk-fake")
    _assert_refused(raised, message)


@pytest.mark.parametrize("case", sorted(BAD_URLS))
def test_openai_compatible_refuses_a_bad_base_url(case: str, clean_env: pytest.MonkeyPatch) -> None:
    pytest.importorskip("openai")
    from forge.models.providers.openai_compatible import OpenAICompatibleProvider

    url, message = BAD_URLS[case]
    with pytest.raises(ConfigurationError) as raised:
        OpenAICompatibleProvider(base_url=url, api_key="sk-fake")
    _assert_refused(raised, message)


@pytest.mark.parametrize("case", sorted(BAD_URLS))
def test_anthropic_refuses_a_bad_sdk_environment_url(
    case: str, clean_env: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("anthropic")
    from forge.models.providers.anthropic import AnthropicProvider

    url, message = BAD_URLS[case]
    clean_env.setenv("ANTHROPIC_BASE_URL", url)
    with pytest.raises(ConfigurationError) as raised:
        AnthropicProvider(api_key="sk-ant-fake")
    _assert_refused(raised, message)


@pytest.mark.parametrize("case", sorted(BAD_URLS))
def test_ollama_refuses_a_bad_base_url(case: str, clean_env: pytest.MonkeyPatch) -> None:
    from forge.models.providers.ollama import OllamaProvider

    url, message = BAD_URLS[case]
    with pytest.raises(ConfigurationError) as raised:
        OllamaProvider(base_url=url)
    _assert_refused(raised, message)

    clean_env.setenv("OLLAMA_BASE_URL", url)
    with pytest.raises(ConfigurationError) as raised:
        OllamaProvider()
    _assert_refused(raised, message)


@pytest.mark.parametrize("case", sorted(BAD_URLS))
def test_the_otel_exporter_refuses_a_bad_endpoint(case: str) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from forge.observability.otel import OTelExporter

    url, message = BAD_URLS[case]
    with pytest.raises(ConfigurationError) as raised:
        OTelExporter(endpoint=url)
    _assert_refused(raised, message)


def test_the_orchestrator_leaves_a_provider_with_a_bad_url_unwired(
    clean_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from forge.orchestration.orchestrator import Orchestrator

    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_BASE_URL", BAD_URLS["query"][0])
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_API_KEY_ENV", "MY_KEY")
    clean_env.setenv("MY_KEY", "sk-fake")

    orchestrator = Orchestrator(config=ForgeConfig.from_env())

    assert "openai_compatible" not in orchestrator.providers


# --- default_query ----------------------------------------------------------------

API_VERSION = "2099-01-01-preview-7d2c"


def test_default_query_is_validated_without_echoing_values() -> None:
    assert validate_default_query({"api-version": API_VERSION}) == {"api-version": API_VERSION}
    assert validate_default_query(None) == {}
    for bad in ({"bad key": "v"}, {"k": ""}, {"k": "x" * 513}, {f"k{i}": "v" for i in range(17)}):
        with pytest.raises(ConfigurationError) as raised:
            validate_default_query(bad)
        assert "x" * 50 not in _rendered(raised.value)


async def test_default_query_reaches_the_endpoint_and_nothing_else(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pytest.importorskip("openai")
    from forge.models.providers.openai_compatible import OpenAICompatibleProvider

    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    with caplog.at_level(logging.DEBUG):
        provider = OpenAICompatibleProvider(
            base_url="https://llm.example.com/v1",
            api_key="sk-fake",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            default_query={"api-version": API_VERSION},
        )
        response = await provider.complete([Message.user("hi")], model="m")
        # The HTTP client's own request line, at INFO, the way httpx writes it.
        logging.getLogger("httpx").info(
            'HTTP Request: %s %s "%s"', "POST", seen[0], "HTTP/1.1 200 OK"
        )

    assert response.content == "ok"
    assert seen and seen[0].params.get("api-version") == API_VERSION
    assert API_VERSION not in caplog.text
    assert "api-version=[redacted]" in caplog.text
    assert API_VERSION not in repr(provider)
    assert API_VERSION not in json.dumps(provider.provider_block.to_event())


def test_the_config_reads_default_query_and_never_shows_its_values(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_DEFAULT_QUERY", f"api-version={API_VERSION}")

    config = ForgeConfig.from_env()

    secret = config.openai_compatible_default_query["api-version"]
    assert secret.get_secret_value() == API_VERSION
    assert API_VERSION not in repr(config)
    assert API_VERSION not in config.model_dump_json()


@pytest.mark.parametrize("raw", ["not form encoded", "a=1&a=2", "=v"])
def test_a_malformed_default_query_variable_is_refused_without_echo(
    raw: str, clean_env: pytest.MonkeyPatch
) -> None:
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_DEFAULT_QUERY", raw.replace("1", API_VERSION))

    with pytest.raises(ConfigurationError) as raised:
        ForgeConfig.from_env()
    assert API_VERSION not in _rendered(raised.value)


def test_the_orchestrator_passes_default_query_to_the_provider(
    clean_env: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openai")
    from forge.orchestration.orchestrator import Orchestrator

    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_BASE_URL", "https://llm.example.com/v1")
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_API_KEY_ENV", "MY_KEY")
    clean_env.setenv("MY_KEY", "sk-fake")
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_DEFAULT_QUERY", f"api-version={API_VERSION}")

    provider: Any = Orchestrator(config=ForgeConfig.from_env()).providers["openai_compatible"]

    assert provider._client.default_query == {"api-version": API_VERSION}
