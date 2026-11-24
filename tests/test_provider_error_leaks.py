"""No provider may repeat what an upstream server said in an error.

Every provider is run against an in-process fake server that reflects the
request's credentials in its error body, for 401, 429 and 500. That is the
worst case: the upstream message itself contains the secret. The secret must
not reach the raised error's message, repr, context, cause or traceback, nor
any log record at INFO or above, nor any of this project's own records.

Third-party DEBUG logging is out of scope: botocore, for one, logs signed
request headers at DEBUG by design, and that is the operator's switch.

Nothing here opens a socket or needs a real key. The HTTP providers use
``httpx.MockTransport`` behind the real SDK; Bedrock uses a real boto3 client
whose ``before-send`` hook answers in place of the network.
"""

from __future__ import annotations

import base64
import json
import sys
import traceback
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from forge.exceptions import ProviderError
from forge.models.base import ModelProvider
from forge.types import Message

_KEY = "sk-fake-leak-test-7c2e41aa-do-not-log"
_AWS_KEY_ID = "AKIAFAKELEAKTEST0001"
_AWS_SECRET = "fake-aws-secret-leak-test-do-not-log"  # noqa: S105 - not a real secret
_STATUSES = [401, 429, 500]
_CREDENTIAL_HEADERS = ("authorization", "x-api-key")


class Reflector:
    """Answers every request with an error that repeats its credentials."""

    def __init__(self, status: int, http: Any = httpx) -> None:
        self.status = status
        self.sent: list[str] = []
        #: The HTTP library the SDK under test is built on.
        self.lib = http

    def message(self, headers: Any) -> str:
        values = []
        for name in _CREDENTIAL_HEADERS:
            value = headers.get(name)
            if isinstance(value, bytes):
                value = value.decode()
            values.append(f"{name}={value}")
        message = "rejected, you sent " + " ".join(values)
        self.sent.append(message)
        return message

    def http(self, request: Any) -> Any:
        message = self.message(request.headers)
        return self.lib.Response(
            self.status,
            json={
                "error": {"message": message, "type": "error"},
                "message": message,
                "type": "error",
            },
        )

    def client(self) -> Any:
        return self.lib.AsyncClient(transport=self.lib.MockTransport(self.http))


def _openai(reflector: Reflector) -> tuple[ModelProvider, list[str]]:
    openai = pytest.importorskip("openai")
    from forge.models.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key=_KEY)
    provider._client = openai.AsyncOpenAI(
        api_key=_KEY,
        base_url="http://localhost:9/v1",
        max_retries=0,
        http_client=reflector.client(),
    )
    return provider, [_KEY]


def _openai_compatible(reflector: Reflector) -> tuple[ModelProvider, list[str]]:
    openai = pytest.importorskip("openai")
    from forge.models.providers.openai_compatible import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        base_url="https://llm.example.com/v1", api_key=_KEY, http_client=reflector.client()
    )
    provider._client = openai.AsyncOpenAI(
        api_key=_KEY,
        base_url="https://llm.example.com/v1",
        max_retries=0,
        http_client=reflector.client(),
    )
    return provider, [_KEY]


def _anthropic(reflector: Reflector) -> tuple[ModelProvider, list[str]]:
    anthropic = pytest.importorskip("anthropic")
    from forge.models.providers.anthropic import AnthropicProvider

    # The SDK may be built on a different HTTP library than this project.
    base = sys.modules["anthropic._base_client"]
    reflector.lib = getattr(base, "httpx2", None) or base.httpx
    provider = AnthropicProvider(api_key=_KEY)
    provider._client = anthropic.AsyncAnthropic(
        api_key=_KEY,
        base_url="http://localhost:9",
        max_retries=0,
        http_client=reflector.client(),
    )
    return provider, [_KEY]


def _ollama(reflector: Reflector) -> tuple[ModelProvider, list[str]]:
    from forge.models.providers.ollama import OllamaProvider

    # Ollama takes no key, but credentials in the URL are sent as Basic auth.
    provider = OllamaProvider(
        base_url=f"http://operator:{_KEY}@localhost:11434",
        transport=httpx.MockTransport(reflector.http),
    )
    basic = base64.b64encode(f"operator:{_KEY}".encode()).decode()
    return provider, [_KEY, basic]


class _Body:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def stream(self, **_: Any) -> Any:
        yield self._body

    def read(self, *_: Any, **__: Any) -> bytes:
        body, self._body = self._body, b""
        return body


_AWS_ERROR_TYPES = {
    401: "UnrecognizedClientException",
    429: "ThrottlingException",
    500: "InternalServerException",
}


def _bedrock(reflector: Reflector) -> tuple[ModelProvider, list[str]]:
    boto3 = pytest.importorskip("boto3")
    from botocore.awsrequest import AWSResponse
    from botocore.config import Config

    from forge.models.providers.bedrock import BedrockProvider

    # Explicit fake credentials: the credential chain is never walked.
    client = boto3.client(
        "bedrock-runtime",
        region_name="us-east-1",
        aws_access_key_id=_AWS_KEY_ID,
        aws_secret_access_key=_AWS_SECRET,
        config=Config(retries={"total_max_attempts": 1}),
    )

    def answer(request: Any, **_: Any) -> Any:
        # Returning a response here means botocore never touches the network.
        body = json.dumps({"message": reflector.message(request.headers)}).encode()
        headers = {
            "content-type": "application/json",
            "x-amzn-errortype": _AWS_ERROR_TYPES[reflector.status],
        }
        return AWSResponse(request.url, reflector.status, headers, _Body(body))

    client.meta.events.register("before-send.bedrock-runtime.*", answer)
    return BedrockProvider(client=client), [_AWS_KEY_ID, _AWS_SECRET]


Factory = Callable[[Reflector], tuple[ModelProvider, list[str]]]
_PROVIDERS: dict[str, Factory] = {
    "openai": _openai,
    "openai_compatible": _openai_compatible,
    "anthropic": _anthropic,
    "ollama": _ollama,
    "bedrock": _bedrock,
}
#: Providers with their own stream(); the others inherit the buffered default.
_NATIVE_STREAM = ("ollama", "bedrock")


def _rendered(error: BaseException) -> str:
    """Everything a logger, an audit sink or a traceback could print."""
    return "\n".join(
        [
            str(error),
            repr(error),
            repr(getattr(error, "context", None)),
            repr(error.__cause__),
            "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        ]
    )


def _assert_nothing_leaked(
    raised: pytest.ExceptionInfo[ProviderError],
    reflector: Reflector,
    secrets: list[str],
    log_text: str,
) -> None:
    # The fake really did put a credential in its answer, so this is not vacuous.
    assert reflector.sent
    assert any(secret in sent for sent in reflector.sent for secret in secrets)

    rendered = _rendered(raised.value)
    for secret in secrets:
        assert secret not in rendered
        assert secret not in log_text
    for sent in reflector.sent:
        assert sent not in rendered
    assert "rejected, you sent" not in rendered
    assert raised.value.__cause__ is None
    # Either nothing was being handled, or the chain to it is cut.
    assert raised.value.__context__ is None or raised.value.__suppress_context__ is True
    assert raised.value.context["status_code"] == reflector.status


def test_every_registered_provider_that_calls_out_is_covered() -> None:
    from forge.models import providers

    exported = {
        cls.name
        for cls in vars(providers).values()
        if isinstance(cls, type) and issubclass(cls, ModelProvider) and cls is not ModelProvider
    }
    # EchoProvider is offline by construction and has no upstream.
    assert exported - {"echo"} == set(_PROVIDERS)


@pytest.mark.parametrize("status", _STATUSES)
@pytest.mark.parametrize("name", sorted(_PROVIDERS))
async def test_a_reflecting_server_cannot_leak_a_credential_through_complete(
    name: str, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    reflector = Reflector(status)
    provider, secrets = _PROVIDERS[name](reflector)

    with (
        caplog.at_level("INFO"),
        caplog.at_level("DEBUG", logger="forge"),
        pytest.raises(ProviderError) as raised,
    ):
        await provider.complete([Message.user("hi")], model="some-model")

    _assert_nothing_leaked(raised, reflector, secrets, caplog.text)


@pytest.mark.parametrize("status", _STATUSES)
@pytest.mark.parametrize("name", _NATIVE_STREAM)
async def test_a_reflecting_server_cannot_leak_a_credential_through_stream(
    name: str, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    reflector = Reflector(status)
    provider, secrets = _PROVIDERS[name](reflector)

    with (
        caplog.at_level("INFO"),
        caplog.at_level("DEBUG", logger="forge"),
        pytest.raises(ProviderError) as raised,
    ):
        async for _ in provider.stream([Message.user("hi")], model="some-model"):
            pass

    _assert_nothing_leaked(raised, reflector, secrets, caplog.text)


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
async def test_an_ollama_transport_failure_does_not_repeat_the_url_or_the_error(
    failure: type[httpx.RequestError],
) -> None:
    from forge.models.providers.ollama import OllamaProvider

    def broken(request: httpx.Request) -> httpx.Response:
        raise failure(f"could not reach {request.url} with {request.headers['authorization']}")

    provider = OllamaProvider(
        base_url=f"http://operator:{_KEY}@localhost:11434/secret-path",
        transport=httpx.MockTransport(broken),
    )

    with pytest.raises(ProviderError) as raised:
        await provider.complete([Message.user("hi")], model="some-model")

    rendered = _rendered(raised.value)
    assert _KEY not in rendered
    assert "secret-path" not in rendered
    assert "could not reach" not in rendered
    assert raised.value.context == {
        "base_url": "http://localhost:11434",
        "error_type": failure.__name__,
    }
    assert raised.value.__suppress_context__ is True


async def test_a_bedrock_error_code_that_is_not_a_code_is_not_repeated() -> None:
    pytest.importorskip("boto3")
    from botocore.exceptions import ClientError

    from forge.models.providers.bedrock import BedrockProvider

    class Client:
        def converse(self, **_: Any) -> Any:
            raise ClientError(
                {
                    "Error": {"Code": f"key was {_KEY}", "Message": f"key was {_KEY}"},
                    "ResponseMetadata": {"HTTPStatusCode": 400},
                },
                "Converse",
            )

    with pytest.raises(ProviderError) as raised:
        await BedrockProvider(client=Client()).complete([Message.user("hi")], model="m")

    assert _KEY not in _rendered(raised.value)
    assert raised.value.context == {
        "model": "m",
        "error_type": "ClientError",
        "status_code": 400,
        "code": "",
    }
