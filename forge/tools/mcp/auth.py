"""OAuth for MCP servers, with Forge's rules on top of the SDK's provider.

Built on the ``mcp`` SDK's ``OAuthClientProvider`` (RFC 9728 protected
resource metadata, RFC 8707 ``resource``, PKCE S256). Forge tightens it in
three places, whatever the SDK's defaults are:

1. **``iss`` is always required.** RFC 9207 lets a client accept an
   authorization response without ``iss`` when the authorization server did
   not advertise ``authorization_response_iss_parameter_supported``; the SDK
   does. Forge does not: a response without ``iss`` is refused before the code
   is redeemed. A present ``iss`` is then compared to the expected issuer by
   exact string comparison (the SDK's check).
2. **Client ID Metadata Documents only.** A ``client_metadata_url`` is
   required. If the authorization server does not support CIMD, or a stored
   client registration is anything else, the flow is refused. Forge never
   falls back to dynamic client registration, and never sends a registration
   request.
3. **Tokens are never configured, logged or exported.** Storage is the
   caller's ``TokenStorage`` (for example a secret store); nothing here writes
   a token anywhere.

**Issuer equivalence (documented, from the SDK).** When the SDK compares the
issuer named in authorization server *metadata* with the expected issuer, it
treats a root issuer with and without its trailing slash
(``https://auth.example`` and ``https://auth.example/``) as the same server
(RFC 8414 section 3.3, legacy path only). Nothing else is normalized. The
``iss`` in an authorization *response* gets no such allowance: it must equal
the metadata's issuer exactly.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx2
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.auth.utils import should_use_client_metadata_url
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

from forge.exceptions import McpAuthError

CallbackHandler = Callable[[], Awaitable[AuthorizationCodeResult]]

MISSING_ISS = (
    "The authorization response has no iss parameter. Forge requires iss on every "
    "authorization response (RFC 9207) and redeems no code without it."
)
NO_CIMD = (
    "The authorization server does not support Client ID Metadata Documents. Forge does not "
    "fall back to dynamic client registration."
)
REGISTRATION_REFUSED = "Forge refused to send a dynamic client registration request."
STORED_REGISTRATION = (
    "The stored client registration is not this client's metadata document URL. Forge uses "
    "Client ID Metadata Documents only; clear the stored registration."
)


def require_iss(handler: CallbackHandler) -> CallbackHandler:
    """Wrap an authorization callback so a response without ``iss`` is refused."""

    async def checked() -> AuthorizationCodeResult:
        result = await handler()
        if not result.iss:
            raise McpAuthError(MISSING_ISS)
        return result

    return checked


def _without_query(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class ForgeOAuthProvider(OAuthClientProvider):
    """``OAuthClientProvider`` that requires ``iss`` and uses CIMD only."""

    def __init__(
        self,
        server_url: str,
        client_metadata: OAuthClientMetadata,
        storage: TokenStorage,
        *,
        client_metadata_url: str,
        redirect_handler: Callable[[str], Awaitable[None]],
        callback_handler: CallbackHandler,
    ) -> None:
        if not client_metadata_url:
            raise McpAuthError("A client_metadata_url (Client ID Metadata Document) is required.")
        try:
            super().__init__(
                server_url,
                client_metadata,
                storage,
                redirect_handler=redirect_handler,
                callback_handler=require_iss(callback_handler),
                client_metadata_url=client_metadata_url,
            )
        except ValueError:
            raise McpAuthError(
                "client_metadata_url must be an HTTPS URL with a non-root path."
            ) from None

    async def _initialize(self) -> None:
        await super()._initialize()
        stored = self.context.client_info
        if stored is not None and stored.client_id != self.context.client_metadata_url:
            raise McpAuthError(STORED_REGISTRATION)

    def _registration_urls(self) -> set[str]:
        urls = {
            urljoin(self.context.get_authorization_base_url(self.context.server_url), "/register")
        }
        metadata = self.context.oauth_metadata
        if metadata is not None and metadata.registration_endpoint is not None:
            urls.add(str(metadata.registration_endpoint))
        return {_without_query(url) for url in urls}

    def _refuse_registration(self, outgoing: httpx2.Request) -> None:
        if outgoing.method != "POST":
            return
        is_registration = _without_query(str(outgoing.url)) in self._registration_urls()
        if is_registration and self.context.client_info is None:
            if self.context.oauth_metadata is not None and not should_use_client_metadata_url(
                self.context.oauth_metadata, self.context.client_metadata_url
            ):
                raise McpAuthError(NO_CIMD)
            raise McpAuthError(REGISTRATION_REFUSED)

    async def _auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        flow = super()._auth_flow(request)
        try:
            outgoing = await flow.__anext__()
            while True:
                self._refuse_registration(outgoing)
                incoming = yield outgoing
                outgoing = await flow.asend(incoming)
        except StopAsyncIteration:
            return
        finally:
            await flow.aclose()
