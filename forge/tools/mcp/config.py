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
import tomllib
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from forge.exceptions import ConfigurationError
from forge.models.endpoints import (
    check_configured_url,
    endpoint_origin,
    hosting_for,
    validate_default_query,
)
from forge.tools.mcp.backend import SELF_CONTAINED, BackendDeclaration
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
    #: What this server's tools depend on: ``"self-contained"`` or a :class:`Backend`.
    #: Required in ``mcp-servers.toml``; ``None`` (undeclared) is refused by
    #: ``forge tools inventory``.
    backend: BackendDeclaration | None = None
    #: Per-tool overrides of ``backend``, keyed by the tool's name on the server.
    tool_backends: dict[str, BackendDeclaration] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", frozen=True)

    def backend_for(self, tool_name: str) -> BackendDeclaration | None:
        """The declared backend of one tool, or ``None`` if nothing was declared."""
        return self.tool_backends.get(tool_name, self.backend)

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


SERVERS_SCHEMA_VERSION = "mcp-servers.v1"
SERVERS_SCHEMA_PATH = Path(__file__).with_name("mcp-servers.schema.json")
MAX_SERVERS_FILE_BYTES = 256 * 1024


def _relative(value: str) -> str:
    if (
        not value
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or PureWindowsPath(value).drive
        or ".." in PurePosixPath(value.replace("\\", "/")).parts
    ):
        raise ValueError("Paths in mcp-servers.toml must be relative and stay beside the file.")
    return value


class McpServersFile(BaseModel):
    """``mcp-servers.v1``: the servers Forge may contact, kept in the user's repository.

    Written as TOML (``mcp-servers.toml``). Only HTTP and stdio servers can be
    named here; ``approvals`` and ``definitions`` are relative to the file.
    """

    schema_version: Literal["mcp-servers.v1"]
    approvals: str = "tool-approvals.json"
    definitions: str = "tool-definitions"
    servers: list[McpServerConfig] = Field(max_length=64)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("approvals", "definitions")
    @classmethod
    def _paths(cls, value: str) -> str:
        return _relative(value)

    @model_validator(mode="after")
    def _servers(self) -> McpServersFile:
        undeclared = [server.server_id for server in self.servers if server.backend is None]
        if undeclared:
            raise ValueError(
                "Every server in mcp-servers.toml declares backend "
                f'("{SELF_CONTAINED}" or a table): {", ".join(undeclared)}.'
            )
        ids = [server.server_id for server in self.servers]
        if len(set(ids)) != len(ids):
            raise ValueError("Two servers in mcp-servers.toml share a server_id.")
        if any(server.transport == "in_process" for server in self.servers):
            raise ValueError("mcp-servers.toml names HTTP and stdio servers only.")
        return self

    def server(self, server_id: str) -> McpServerConfig:
        for server in self.servers:
            if server.server_id == server_id:
                return server
        raise ConfigurationError(
            "No server with that server_id is configured.", context={"server_id": server_id}
        )

    @classmethod
    def load(cls, path: Path) -> tuple[McpServersFile, Path]:
        """Read the file; returns it with the directory its relative paths resolve against."""
        if path.is_symlink() or not path.is_file():
            raise ConfigurationError(
                "mcp-servers.toml is missing or is a link.", context={"path": path.name}
            )
        if path.stat().st_size > MAX_SERVERS_FILE_BYTES:
            raise ConfigurationError("mcp-servers.toml is over 256 KiB.")
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            raise ConfigurationError("mcp-servers.toml is not valid UTF-8 TOML.") from None
        return cls.model_validate(data), path.parent
