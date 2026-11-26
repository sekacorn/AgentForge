"""Expose Forge tools as an MCP server, under the owner's exposure rules.

Rules (Phase 4 design, section 10), each enforced when the server is built,
before anything listens:

* **Off by default.** Nothing in Forge starts a server; an operator builds an
  :class:`McpToolServer` (or runs ``forge mcp serve``) and starts it.
* **stdio or loopback only**, unless ``allow_non_loopback=True`` is set.
* **HTTP requires authorization**: a bearer token read from a named
  environment variable, or an OAuth ``token_verifier``. Without either, an
  HTTP server refuses to start, whatever the bind address.
* **Default-deny per tool.** Only tools named in ``expose`` are listed or
  callable. There is no "expose all", and an empty list refuses to start.
* **Dangerous tools never by default.** A ``dangerous=True`` tool (``http_get``,
  sandboxed execution, any MCP tool Forge consumes) is refused unless it is
  also named in ``allow_dangerous``. Listing it in ``expose`` is not enough.
* **Every inbound call is audited**, refused ones included, as
  ``agentforge.mcp.server_call``: caller, tool, outcome, duration, arguments
  with credentials and PII redacted and capped, any injection indicators, and
  the result's SHA-256 and length, never its content.
* **Caller input is untrusted.** Arguments are bounded in size, cleaned of
  control characters, validated against the tool's schema, screened for
  injection indicators, and run under the same sandbox policy and timeout as a
  model-requested call. A failure reaches the caller as its exception class
  only, never internal text.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import time
from collections.abc import Mapping
from typing import Any, Final, Literal

import mcp_types as types
from jsonschema import Draft202012Validator
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.lowlevel import Server
from pydantic import BaseModel, ConfigDict, Field, model_validator
from referencing import Registry
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from forge.compliance.audit import AuditLogger
from forge.compliance.redaction import PIIRedactor
from forge.config import SecurityConfig
from forge.exceptions import ConfigurationError, ForgeError
from forge.security.sandbox import ToolSandbox
from forge.security.sanitization import InputSanitizer
from forge.security.secrets import redact_secrets_in
from forge.tools.base import Tool
from forge.tools.registry import ToolRegistry

AUDIT_SERVER_CALL: Final = "agentforge.mcp.server_call"
MAX_ARGUMENT_BYTES: Final = 64 * 1024
MAX_AUDITED_ARGUMENT_CHARS: Final = 2048
GENERIC_REFUSAL: Final = "The call was refused."


class McpServeConfig(BaseModel):
    """How a Forge tool server may run. Every unsafe choice has to be spelled out."""

    transport: Literal["stdio", "streamable_http"] = "stdio"
    #: The only tools the server lists or runs, by name. Required, never "all".
    expose: list[str] = Field(default_factory=list)
    #: Dangerous tools that may be exposed. Each must also be in ``expose``.
    allow_dangerous: list[str] = Field(default_factory=list)
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    allow_non_loopback: bool = False
    #: Name of the environment variable holding the bearer token HTTP callers must send.
    bearer_token_env: str | None = None
    #: OAuth resource-server settings, used with a ``token_verifier``.
    oauth_issuer_url: str | None = None
    oauth_resource_url: str | None = None
    required_scopes: list[str] = Field(default_factory=list)
    tool_timeout_seconds: float = Field(default=30.0, gt=0, le=600)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def _shape(self) -> McpServeConfig:
        if len(set(self.expose)) != len(self.expose):
            raise ValueError("expose names a tool twice.")
        stray = sorted(set(self.allow_dangerous) - set(self.expose))
        if stray:
            raise ValueError(
                f"allow_dangerous names tools that are not exposed: {', '.join(stray)}."
            )
        if self.transport == "stdio" and (
            self.bearer_token_env or self.oauth_issuer_url or self.allow_non_loopback
        ):
            raise ValueError("Network and authorization settings apply to streamable_http only.")
        return self


def _redacted_arguments(arguments: Mapping[str, Any], redactor: PIIRedactor) -> str:
    cleaned = redact_secrets_in(redactor.redact_obj(dict(arguments)))
    text = json.dumps(cleaned, ensure_ascii=False, default=str, sort_keys=True)
    return (
        text
        if len(text) <= MAX_AUDITED_ARGUMENT_CHARS
        else text[: MAX_AUDITED_ARGUMENT_CHARS - 1] + "…"
    )


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


class McpToolServer:
    """An MCP server for the named tools of one :class:`ToolRegistry`."""

    def __init__(
        self,
        registry: ToolRegistry,
        config: McpServeConfig,
        *,
        audit: AuditLogger,
        sanitizer: InputSanitizer | None = None,
        token_verifier: TokenVerifier | None = None,
        name: str = "forge",
    ) -> None:
        self.config = config
        self._audit = audit
        self._sanitizer = sanitizer or InputSanitizer()
        self._redactor = PIIRedactor()
        self._token_verifier = token_verifier
        if not config.expose:
            raise ConfigurationError("Nothing to expose: name each tool to serve (expose).")
        missing = [tool for tool in config.expose if not registry.has(tool)]
        if missing:
            raise ConfigurationError(
                "expose names tools that are not registered.", context={"tools": missing}
            )
        self._tools: dict[str, Tool] = {name: registry.get(name) for name in config.expose}
        blocked = [
            name
            for name, tool in self._tools.items()
            if tool.dangerous and name not in config.allow_dangerous
        ]
        if blocked:
            raise ConfigurationError(
                "Dangerous tools are never exposed by default; name each one in allow_dangerous.",
                context={"tools": blocked},
            )
        self._check_network()
        self._sandbox = ToolSandbox(
            SecurityConfig(
                allow_tools=list(self._tools), tool_timeout_seconds=config.tool_timeout_seconds
            )
        )
        self.server: Server[Any] = Server(
            name, on_list_tools=self._list_tools, on_call_tool=self._call_tool
        )

    # --- the rules that must hold before anything listens ------------------------------------

    def _check_network(self) -> None:
        config = self.config
        if config.transport != "streamable_http":
            return
        if not config.allow_non_loopback and not _is_loopback(config.host):
            raise ConfigurationError(
                "The server binds to loopback only unless allow_non_loopback is set.",
                context={"host": config.host},
            )
        has_bearer = config.bearer_token_env is not None
        has_oauth = self._token_verifier is not None
        if has_bearer == has_oauth:
            raise ConfigurationError(
                "An HTTP server requires authorization: set exactly one of bearer_token_env "
                "or an OAuth token_verifier."
            )
        if has_bearer:
            assert config.bearer_token_env is not None  # nosec B101
            if not os.environ.get(config.bearer_token_env):
                raise ConfigurationError(
                    "The bearer token environment variable is not set.",
                    context={"variable": config.bearer_token_env},
                )
        if has_oauth and not (config.oauth_issuer_url and config.oauth_resource_url):
            raise ConfigurationError("OAuth needs oauth_issuer_url and oauth_resource_url.")

    # --- MCP handlers ----------------------------------------------------------------------

    async def _list_tools(self, _ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(name=name, description=tool.description, input_schema=tool.parameters)
                for name, tool in self._tools.items()
            ]
        )

    def _caller(self) -> str:
        if self.config.transport == "stdio":
            return "stdio"
        token = get_access_token()
        if token is not None:
            return f"oauth:{token.client_id}"
        return "bearer"

    def _record(self, caller: str, tool: str, outcome: str, started: float, **details: Any) -> None:
        self._audit.record(
            AUDIT_SERVER_CALL,
            actor=caller,
            resource=tool,
            outcome=outcome,
            duration_ms=round((time.monotonic() - started) * 1000, 3),
            transport=self.config.transport,
            **details,
        )

    def _refuse(
        self, caller: str, tool: str, started: float, reason: str, **details: Any
    ) -> types.CallToolResult:
        self._record(caller, tool, "refused", started, reason=reason, **details)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"{GENERIC_REFUSAL} {reason}")],
            is_error=True,
        )

    async def _call_tool(self, _ctx: Any, params: Any) -> types.CallToolResult:
        started = time.monotonic()
        caller = self._caller()
        name = str(params.name)[:128]
        raw = params.arguments or {}
        if name not in self._tools:
            return self._refuse(caller, name, started, "No such tool is exposed.")
        tool = self._tools[name]
        if not isinstance(raw, dict):
            return self._refuse(caller, name, started, "Arguments must be an object.")
        try:
            size = len(
                json.dumps(raw, ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")
            )
        except ValueError:
            return self._refuse(caller, name, started, "Arguments are not plain JSON.")
        if size > MAX_ARGUMENT_BYTES:
            return self._refuse(
                caller, name, started, "Arguments are too large.", argument_bytes=size
            )
        try:
            arguments = self._clean(raw)
        except ForgeError:
            return self._refuse(caller, name, started, "An argument is too long.")
        audited = _redacted_arguments(arguments, self._redactor)
        validator = Draft202012Validator(tool.parameters, registry=Registry())
        problems = sorted(validator.iter_errors(arguments), key=lambda e: list(e.absolute_path))
        if problems:
            where = ["/".join(str(p) for p in e.absolute_path) or "(root)" for e in problems[:5]]
            return self._refuse(
                caller,
                name,
                started,
                f"Arguments do not match the input schema at: {', '.join(where)}.",
                arguments=audited,
            )
        flags = sorted(
            {
                reason
                for text in _strings(arguments)
                for reason in self._sanitizer.scan(text).reasons
            }
        )
        allowed, why = self._sandbox.is_allowed(tool)
        if not allowed:
            return self._refuse(
                caller, name, started, "Denied by policy.", policy=why, arguments=audited
            )
        try:
            content = await asyncio.wait_for(
                tool.invoke(arguments), timeout=self.config.tool_timeout_seconds
            )
        except TimeoutError:
            self._record(
                caller,
                name,
                "error",
                started,
                error="TimeoutError",
                arguments=audited,
                argument_flags=flags,
            )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="The tool timed out.")], is_error=True
            )
        except Exception as exc:  # noqa: BLE001 - every failure reaches the caller as its class only
            self._record(
                caller,
                name,
                "error",
                started,
                error=type(exc).__name__,
                arguments=audited,
                argument_flags=flags,
            )
            return types.CallToolResult(
                content=[
                    types.TextContent(type="text", text=f"The tool failed ({type(exc).__name__}).")
                ],
                is_error=True,
            )
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self._record(
            caller,
            name,
            "ok",
            started,
            arguments=audited,
            argument_flags=flags,
            result_sha256=digest,
            result_chars=len(content),
        )
        return types.CallToolResult(content=[types.TextContent(type="text", text=content)])

    def _clean(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._sanitizer.sanitize(value)
        if isinstance(value, dict):
            return {str(key): self._clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._clean(item) for item in value]
        return value

    # --- running -----------------------------------------------------------------------------

    async def run_stdio(self) -> None:
        """Serve over this process's stdin and stdout until the client disconnects."""
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream, write_stream, self.server.create_initialization_options()
            )

    def http_app(self) -> ASGIApp:
        """The Streamable HTTP ASGI app, with authorization in front of every request."""
        if self.config.transport != "streamable_http":
            raise ConfigurationError("http_app needs transport streamable_http.")
        if self._token_verifier is not None:
            assert self.config.oauth_issuer_url and self.config.oauth_resource_url  # nosec B101
            auth = AuthSettings(
                issuer_url=self.config.oauth_issuer_url,
                resource_server_url=self.config.oauth_resource_url,
                required_scopes=self.config.required_scopes or None,
                # RFC 8707: refuse a token issued for any other resource.
                validate_token_resource=True,
            )
            app: ASGIApp = self.server.streamable_http_app(
                host=self.config.host, auth=auth, token_verifier=self._token_verifier
            )
            # The SDK refuses bad tokens before any handler runs; record those refusals too.
            return _AuditRefusals(app, self._audit)
        assert self.config.bearer_token_env is not None  # nosec B101
        token = os.environ.get(self.config.bearer_token_env, "")
        inner: ASGIApp = self.server.streamable_http_app(host=self.config.host)
        return _BearerGate(inner, token, self._audit)

    async def run_http(self) -> None:  # pragma: no cover - needs a real socket
        import uvicorn

        app = self.http_app()
        server = uvicorn.Server(
            uvicorn.Config(app, host=self.config.host, port=self.config.port, log_level="warning")
        )
        await server.serve()


def _is_loopback(host: str) -> bool:
    """``localhost`` or a loopback address. A private network address is not loopback."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


class _BearerGate:
    """Refuse any HTTP request that does not carry the configured bearer token."""

    def __init__(self, app: ASGIApp, token: str, audit: AuditLogger) -> None:
        if not token:
            raise ConfigurationError("The bearer token is empty.")
        self._app = app
        self._expected = token.encode("utf-8")
        self._audit = audit
        # The SDK app's lifespan starts its session manager; the gate passes it through.
        self.router = getattr(app, "router", None)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get("authorization", b"")
        scheme, _, credential = supplied.partition(b" ")
        if scheme.lower() != b"bearer" or not hmac.compare_digest(
            credential.strip(), self._expected
        ):
            self._audit.record(
                AUDIT_SERVER_CALL,
                actor="unauthenticated",
                resource=str(scope.get("path", ""))[:128],
                outcome="refused",
                reason="missing or invalid bearer token",
                transport="streamable_http",
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"www-authenticate", b"Bearer"), (b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"Unauthorized"})
            return
        await self._app(scope, receive, send)


class _AuditRefusals:
    """Record every HTTP request the wrapped app answers with 401 or 403."""

    def __init__(self, app: ASGIApp, audit: AuditLogger) -> None:
        self._app = app
        self._audit = audit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        async def watched(message: Message) -> None:
            if message.get("type") == "http.response.start" and message.get("status") in (401, 403):
                self._audit.record(
                    AUDIT_SERVER_CALL,
                    actor="unauthenticated",
                    resource=str(scope.get("path", ""))[:128],
                    outcome="refused",
                    reason=f"authorization failed (HTTP {message.get('status')})",
                    transport="streamable_http",
                )
            await send(message)

        await self._app(scope, receive, watched)
