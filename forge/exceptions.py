"""Exception hierarchy for Forge.

A single rooted hierarchy (everything derives from :class:`ForgeError`) lets
callers catch broadly (``except ForgeError``) or narrowly (``except
BudgetExceededError``) without guessing at error types. Each exception carries
optional structured context that is safe to log and to surface in audit trails.
"""

from __future__ import annotations

from typing import Any


class ForgeError(Exception):
    """Base class for every error raised by Forge.

    Parameters
    ----------
    message:
        Human-readable description of what went wrong.
    context:
        Optional structured metadata (model name, agent id, tool name, ...).
        Keep this free of secrets — it may be written to logs and audit records.
    """

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = context or {}

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.context:
            return f"{self.message} (context={self.context})"
        return self.message


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
class ConfigurationError(ForgeError):
    """Invalid, missing, or inconsistent configuration."""


# --------------------------------------------------------------------------- #
# Model providers & routing
# --------------------------------------------------------------------------- #
class ProviderError(ForgeError):
    """Base class for errors originating from a model provider."""


class ProviderAuthError(ProviderError):
    """Authentication failed (missing or invalid API key)."""


class ProviderRateLimitError(ProviderError):
    """The provider rejected the request because of rate limiting."""


class ProviderResponseError(ProviderError):
    """The provider returned an unexpected or malformed response."""


class ModelRoutingError(ForgeError):
    """No model could be selected that satisfies the routing constraints."""


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
class ToolError(ForgeError):
    """Base class for tool-related errors."""


class ToolNotFoundError(ToolError):
    """A requested tool is not registered."""


class ToolValidationError(ToolError):
    """Tool arguments failed validation against the tool's schema."""


class ToolExecutionError(ToolError):
    """A tool raised an exception while executing."""


class ToolTimeoutError(ToolError):
    """A tool exceeded its execution time budget."""


# --------------------------------------------------------------------------- #
# Agents & orchestration
# --------------------------------------------------------------------------- #
class AgentError(ForgeError):
    """Base class for agent execution errors."""


class MaxStepsExceededError(AgentError):
    """An agent exceeded its maximum number of reasoning/acting steps."""


class OrchestrationError(ForgeError):
    """An error occurred while coordinating multiple agents."""


# --------------------------------------------------------------------------- #
# Governance: budgets, security, compliance
# --------------------------------------------------------------------------- #
class BudgetExceededError(ForgeError):
    """A run exceeded its configured cost or token budget."""


class SecurityError(ForgeError):
    """Base class for security policy violations."""


class PromptInjectionError(SecurityError):
    """Input tripped the prompt-injection heuristics."""


class AccessDeniedError(SecurityError):
    """An actor attempted an action they are not authorized to perform."""


class MemoryBackendError(ForgeError):
    """A memory/RAG backend failed."""
