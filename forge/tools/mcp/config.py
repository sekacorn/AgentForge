"""Configuration for one MCP server. No ``mcp`` import: safe in core code paths.

A server Forge may contact is named here, and only here. Its endpoint follows
the same URL rule as model endpoints: no credentials, query string or
fragment; parameters an endpoint needs go in ``default_query`` and are
redacted from logs. Records name the endpoint by origin only, with hosting
derived from it by the shared locality rule.

A stdio server is launched from an argument list, never a shell, and receives
only the environment variables named in ``env_allowlist``. Secrets are passed
by naming the environment variable that holds them, never by value.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from forge.exceptions import ConfigurationError
from forge.models.endpoints import (
    check_configured_url,
    endpoint_origin,
    hosting_for,
    validate_default_query,
)
from forge.tools.mcp.bounds import SERVER_ID

Transport = Literal["streamable_http", "stdio", "in_process"]

#: Always passed to a stdio server so it can start; nothing else is inherited.
_BASE_ENV = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG")


class McpServerConfig(BaseModel):
    """One MCP server Forge is allowed to contact."""

    server_id: str
    transport: Transport
    #: ``streamable_http`` only.
    url: str | None = None
    default_query: dict[str, str] = Field(default_factory=dict)
    #: Name of an environment variable holding a bearer token (``streamable_http``).
    bearer_token_env: str | None = None
    #: ``stdio`` only: the program and its arguments, run without a shell.
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env_allowlist: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("server_id")
    @classmethod
    def _server_id(cls, value: str) -> str:
        if not SERVER_ID.fullmatch(value):
            raise ValueError("server_id must match [a-z0-9][a-z0-9_-]{0,31}.")
        return value

    @model_validator(mode="after")
    def _transport_fields(self) -> McpServerConfig:
        if self.transport == "streamable_http":
            if not self.url:
                raise ValueError("A streamable_http server needs a url.")
            check_configured_url(self.url)
            validate_default_query(self.default_query)
            if self.command or self.args or self.env_allowlist:
                raise ValueError("command, args and env_allowlist apply to stdio servers only.")
        elif self.transport == "stdio":
            if not self.command:
                raise ValueError("A stdio server needs a command.")
            if self.url or self.default_query or self.bearer_token_env:
                raise ValueError(
                    "url, default_query and bearer_token_env apply to HTTP servers only."
                )
        elif self.url or self.command:
            raise ValueError("An in_process server takes no url or command.")
        return self

    @property
    def origin(self) -> str | None:
        """The endpoint's origin, safe for records; ``None`` for stdio and in-process."""
        return endpoint_origin(self.url) if self.url else None

    @property
    def hosting(self) -> str:
        """``local`` or ``hosted``, derived from the endpoint; stdio and in-process are local."""
        return hosting_for(self.url) if self.url else "local"

    @property
    def namespace(self) -> str:
        """The policy namespace for this server's tools."""
        return f"mcp.{self.server_id}"

    def stdio_environment(self) -> dict[str, str]:
        """The environment a stdio server receives: the base set plus the allowlist."""
        names = (*_BASE_ENV, *self.env_allowlist)
        return {name: os.environ[name] for name in names if name in os.environ}

    def bearer_token(self) -> str | None:
        """The bearer token, read from the named environment variable at connect time."""
        if self.bearer_token_env is None:
            return None
        token = os.environ.get(self.bearer_token_env)
        if not token:
            raise ConfigurationError(
                "The bearer token environment variable is not set.",
                context={"server_id": self.server_id, "variable": self.bearer_token_env},
            )
        return token
