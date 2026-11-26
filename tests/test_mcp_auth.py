"""Forge's OAuth rules for MCP servers, end to end against a mock authorization server.

``httpx2.MockTransport`` answers every request in-process, so no socket is
opened. Each test records the requests the flow made, which is how "never
sends a registration request" and "redeems no code without iss" are shown.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("mcp")

import httpx2  # noqa: E402
from mcp.client.auth import OAuthFlowError  # noqa: E402
from mcp.client.auth.utils import issuers_match  # noqa: E402
from mcp.shared.auth import (  # noqa: E402
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

from forge.exceptions import McpAuthError  # noqa: E402
from forge.tools.mcp.auth import (  # noqa: E402
    MISSING_ISS,
    NO_CIMD,
    REGISTRATION_REFUSED,
    STORED_REGISTRATION,
    ForgeOAuthProvider,
    require_iss,
)

MCP_URL = "https://mcp.example.com/mcp"
ISSUER = "https://auth.example.com"
CIMD_URL = "https://forge.example.org/oauth/client.json"


class MemoryStorage:
    def __init__(self, client_info: OAuthClientInformationFull | None = None) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info = client_info

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


class AuthServer:
    """A protected MCP resource plus its authorization server."""

    def __init__(self, *, cimd: bool = True, metadata: bool = True) -> None:
        self.cimd = cimd
        self.metadata = metadata
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url).split("?")[0]
        self.requests.append((request.method, url))
        if url == MCP_URL:
            if request.headers.get("Authorization") == "Bearer access-1":
                return httpx2.Response(200, json={"ok": True})
            return httpx2.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"'
                },
            )
        if url.startswith("https://mcp.example.com/.well-known/oauth-protected-resource"):
            return httpx2.Response(
                200, json={"resource": MCP_URL, "authorization_servers": [ISSUER]}
            )
        if url.startswith(ISSUER + "/.well-known/"):
            if not self.metadata:
                return httpx2.Response(404)
            return httpx2.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": ISSUER + "/authorize",
                    "token_endpoint": ISSUER + "/token",
                    "registration_endpoint": ISSUER + "/register",
                    "response_types_supported": ["code"],
                    "code_challenge_methods_supported": ["S256"],
                    "client_id_metadata_document_supported": self.cimd,
                },
            )
        if url == ISSUER + "/token":
            return httpx2.Response(
                200, json={"access_token": "access-1", "token_type": "Bearer", "expires_in": 3600}
            )
        if url.endswith("/register"):
            return httpx2.Response(
                201, json={"client_id": "dcr-client", "redirect_uris": ["http://127.0.0.1:9/cb"]}
            )
        return httpx2.Response(404)

    def posted(self, suffix: str) -> bool:
        return any(method == "POST" and url.endswith(suffix) for method, url in self.requests)


def _provider(storage: MemoryStorage, *, iss: str | None) -> ForgeOAuthProvider:
    redirected: list[str] = []

    async def redirect(url: str) -> None:
        redirected.append(url)

    async def callback() -> AuthorizationCodeResult:
        state = parse_qs(urlsplit(redirected[-1]).query)["state"][0]
        return AuthorizationCodeResult(code="code-1", state=state, iss=iss)

    return ForgeOAuthProvider(
        MCP_URL,
        OAuthClientMetadata(redirect_uris=["http://127.0.0.1:9/cb"], client_name="forge"),
        storage,
        client_metadata_url=CIMD_URL,
        redirect_handler=redirect,
        callback_handler=callback,
    )


async def _get(server: AuthServer, provider: ForgeOAuthProvider) -> httpx2.Response:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(server), auth=provider) as client:
        return await client.get(MCP_URL)


async def test_cimd_with_iss_completes_without_registering() -> None:
    server, storage = AuthServer(), MemoryStorage()
    response = await _get(server, _provider(storage, iss=ISSUER))
    assert response.status_code == 200
    assert server.posted("/token") and not server.posted("/register")
    assert storage.client_info is not None and storage.client_info.client_id == CIMD_URL


async def test_a_response_without_iss_is_refused_before_the_code_is_redeemed() -> None:
    server = AuthServer()  # the AS does not advertise iss support, so the SDK alone would accept it
    with pytest.raises(McpAuthError) as caught:
        await _get(server, _provider(MemoryStorage(), iss=None))
    assert str(caught.value) == MISSING_ISS
    assert not server.posted("/token")


@pytest.mark.parametrize("iss", ["https://evil.example.com", ISSUER + "/"])
async def test_a_mismatched_iss_is_refused_by_exact_comparison(iss: str) -> None:
    server = AuthServer()
    with pytest.raises(OAuthFlowError, match="iss mismatch"):
        await _get(server, _provider(MemoryStorage(), iss=iss))
    assert not server.posted("/token")


async def test_no_cimd_support_is_refused_without_registering() -> None:
    server = AuthServer(cimd=False)
    with pytest.raises(McpAuthError) as caught:
        await _get(server, _provider(MemoryStorage(), iss=ISSUER))
    assert str(caught.value) == NO_CIMD
    assert not server.posted("/register") and not server.posted("/token")


async def test_missing_metadata_never_falls_back_to_registration() -> None:
    server = AuthServer(metadata=False)
    with pytest.raises(McpAuthError) as caught:
        await _get(server, _provider(MemoryStorage(), iss=ISSUER))
    assert str(caught.value) == REGISTRATION_REFUSED
    assert not server.posted("/register")


async def test_a_stored_dynamic_registration_is_refused() -> None:
    stored = OAuthClientInformationFull(
        client_id="dcr-client", redirect_uris=["http://127.0.0.1:9/cb"]
    )
    server = AuthServer()
    with pytest.raises(McpAuthError) as caught:
        await _get(server, _provider(MemoryStorage(stored), iss=ISSUER))
    assert str(caught.value) == STORED_REGISTRATION
    assert server.requests == []


@pytest.mark.parametrize(
    "url", ["", "http://forge.example.org/client.json", "https://forge.example.org/"]
)
def test_a_client_metadata_url_is_required_and_must_be_https_with_a_path(url: str) -> None:
    async def noop(_url: str) -> None:
        return None

    async def callback() -> AuthorizationCodeResult:  # pragma: no cover - never reached
        raise AssertionError

    with pytest.raises(McpAuthError):
        ForgeOAuthProvider(
            MCP_URL,
            OAuthClientMetadata(redirect_uris=["http://127.0.0.1:9/cb"]),
            MemoryStorage(),
            client_metadata_url=url,
            redirect_handler=noop,
            callback_handler=callback,
        )


async def test_require_iss_passes_a_response_that_has_one() -> None:
    async def handler() -> AuthorizationCodeResult:
        return AuthorizationCodeResult(code="c", state="s", iss=ISSUER)

    assert (await require_iss(handler)()).iss == ISSUER


def test_documented_issuer_equivalence_is_metadata_only() -> None:
    """The SDK treats a root issuer with and without a trailing slash as one server (RFC 8414 3.3).

    Forge documents this rather than changing it; it applies to authorization
    server *metadata* on the legacy path. An authorization *response* ``iss``
    gets no such allowance (see the mismatch test above).
    """
    assert issuers_match("https://auth.example.com", "https://auth.example.com/")
    assert not issuers_match("https://auth.example.com/a", "https://auth.example.com/a/")
    assert not issuers_match("https://auth.example.com", "https://AUTH.example.com/x")
