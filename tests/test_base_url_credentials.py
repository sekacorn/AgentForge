"""A base_url that carries credentials is refused, never used.

HTTP clients log the request URL, so a password placed in the URL would reach
the logs on every call. The refusal itself must not echo the URL.
"""

from __future__ import annotations

import pytest

from forge.exceptions import ConfigurationError
from forge.models.endpoints import url_has_credentials

pytest.importorskip("openai")

from forge.models.providers.openai import OpenAIProvider  # noqa: E402
from forge.models.providers.openai_compatible import OpenAICompatibleProvider  # noqa: E402

_SECRET = "url-secret-9d3f-do-not-log"
_URLS_WITH_CREDENTIALS = [
    f"https://user:{_SECRET}@llm.example.com/v1",
    f"https://{_SECRET}@llm.example.com/v1",
    f"https://:{_SECRET}@llm.example.com/v1",
    f"http://user:{_SECRET}@localhost:8000/v1",
    f"https://user:{_SECRET}@[::1]:8000/v1",
    f"https://user:{_SECRET}@llm.example.com:notaport/v1",
]
_CLEAN_URLS = [
    "https://llm.example.com/v1",
    "http://localhost:8000/v1",
    "https://llm.example.com/v1/path@with-at",
    "https://llm.example.com/v1?user=a@b",
]


@pytest.mark.parametrize("url", _URLS_WITH_CREDENTIALS)
def test_credentials_in_a_url_are_detected(url: str) -> None:
    assert url_has_credentials(url)


@pytest.mark.parametrize("url", _CLEAN_URLS)
def test_an_at_sign_outside_the_authority_is_not_a_credential(url: str) -> None:
    assert not url_has_credentials(url)


def _assert_refused_without_echo(raised: pytest.ExceptionInfo[ConfigurationError]) -> None:
    rendered = f"{raised.value} {raised.value!r} {raised.value.context!r}"
    assert "must not contain credentials" in str(raised.value)
    assert _SECRET not in rendered
    assert "user" not in raised.value.context["endpoint"]


@pytest.mark.parametrize("url", _URLS_WITH_CREDENTIALS)
def test_openai_provider_refuses_credentials_in_base_url(url: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        OpenAIProvider(api_key="sk-fake", base_url=url)

    _assert_refused_without_echo(raised)


def test_openai_provider_refuses_credentials_in_the_sdk_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The SDK reads OPENAI_BASE_URL on its own when no base_url is passed.
    monkeypatch.setenv("OPENAI_BASE_URL", _URLS_WITH_CREDENTIALS[0])

    with pytest.raises(ConfigurationError) as raised:
        OpenAIProvider(api_key="sk-fake")

    _assert_refused_without_echo(raised)


@pytest.mark.parametrize("url", _URLS_WITH_CREDENTIALS[:5])
def test_openai_compatible_provider_refuses_credentials_in_base_url(url: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        OpenAICompatibleProvider(base_url=url, api_key="sk-fake")

    _assert_refused_without_echo(raised)


def test_openai_compatible_provider_refuses_credentials_in_a_user_preset(tmp_path) -> None:
    import json

    path = tmp_path / "presets.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "provider-presets.v1",
                "presets": {
                    "company_gateway": {
                        "base_url": _URLS_WITH_CREDENTIALS[0],
                        "docs_url": "https://docs.internal.example.com/llm",
                        "jurisdiction": "DE",
                        "hosting": "hosted",
                        "api_key_env": "INTERNAL_LLM_KEY",
                        "token_param": "max_tokens",
                        "compatibility": "documented",
                        "review": "unreviewed",
                        "reviewed_on": None,
                        "notes": "",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as raised:
        OpenAICompatibleProvider(
            preset="company_gateway", api_key="sk-fake", presets_path=str(path)
        )

    _assert_refused_without_echo(raised)


@pytest.mark.parametrize("url", _CLEAN_URLS[:2])
def test_a_url_without_credentials_still_works(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    assert OpenAIProvider(api_key="sk-fake", base_url=url) is not None
    assert OpenAICompatibleProvider(base_url=url, api_key="sk-fake") is not None


def test_the_orchestrator_leaves_the_provider_unwired(monkeypatch: pytest.MonkeyPatch) -> None:
    from forge.config import ForgeConfig
    from forge.orchestration.orchestrator import Orchestrator

    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FORGE_OPENAI_COMPATIBLE_BASE_URL", _URLS_WITH_CREDENTIALS[0])
    monkeypatch.setenv("FORGE_OPENAI_COMPATIBLE_API_KEY_ENV", "MY_KEY")
    monkeypatch.setenv("MY_KEY", "sk-fake")

    orchestrator = Orchestrator(config=ForgeConfig.from_env())

    assert "openai_compatible" not in orchestrator.providers
