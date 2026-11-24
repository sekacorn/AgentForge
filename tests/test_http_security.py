"""Security tests for the ``http_get`` tool: SSRF mitigation and a real size cap.

Hermetic: the few tests that need a server response drive ``http_get`` through an
``httpx.MockTransport`` (no network). The host-block tests make no request at all.
``http_get`` is a ``Tool`` (via the ``@tool`` decorator), so it is run through
``.invoke``.
"""

from __future__ import annotations

import httpx
import pytest

from forge.tools.builtin import http as http_mod
from forge.tools.builtin.http import _MAX_BYTES, http_get


def _use_mock(monkeypatch, handler) -> None:
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr(http_mod.httpx, "AsyncClient", factory)


async def _fetch(url: str) -> str:
    return await http_get.invoke({"url": url})


async def test_blocks_cloud_metadata_host() -> None:
    result = await _fetch("http://169.254.169.254/latest/meta-data/")
    assert "refusing" in result.lower()


async def test_blocks_loopback_host() -> None:
    result = await _fetch("http://localhost:8080/admin")
    assert "refusing" in result.lower()


async def test_rejects_non_http_scheme() -> None:
    result = await _fetch("file:///etc/passwd")
    assert "only http(s)" in result


async def test_does_not_follow_redirects(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"Location": "http://169.254.169.254/"}, content=b"redirecting"
        )

    _use_mock(monkeypatch, handler)
    result = await _fetch("http://example.com/")
    # The 302 is returned verbatim — the redirect to an internal host is NOT followed.
    assert result.startswith("HTTP 302")


async def test_caps_response_size(monkeypatch) -> None:
    oversized = b"x" * (_MAX_BYTES * 3)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized)

    _use_mock(monkeypatch, handler)
    result = await _fetch("http://example.com/big")
    assert "[truncated]" in result
    # Output is bounded near the cap, not the full oversized body.
    assert len(result) < _MAX_BYTES + 100


_URL_TOKEN = "url-token-51c8-do-not-log"


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.InvalidURL],
)
async def test_a_failed_request_does_not_repeat_the_url_or_the_error_text(
    monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    # A URL can carry a token; the tool result goes back to the model.
    def handler(request: httpx.Request) -> httpx.Response:
        raise failure(f"could not fetch {request.url}")

    _use_mock(monkeypatch, handler)
    result = await _fetch(f"http://example.com/data?access_token={_URL_TOKEN}")

    assert result == f"Error: request failed ({failure.__name__})"
    assert _URL_TOKEN not in result


async def test_an_unparseable_url_is_refused_without_raising() -> None:
    result = await _fetch(f"http://[not-an-address/{_URL_TOKEN}")

    assert result == "Error: the URL could not be parsed."
