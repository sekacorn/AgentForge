"""The model-provider abstraction.

Everything that can answer a chat completion implements :class:`ModelProvider`.
The rest of Forge depends only on this interface, which is what makes the
platform genuinely multi-provider: swapping Anthropic for a local model is a
matter of registering a different provider, not rewriting agents.

Providers are responsible for translating between Forge's provider-agnostic
:class:`~forge.types.Message` vocabulary and their own wire format. They report
token usage but **not** dollar cost — pricing lives in the
:class:`~forge.models.registry.ModelRegistry` so it stays in one place.
"""

from __future__ import annotations

import abc

from forge.types import Message, ModelResponse, Role, ToolSchema


def split_system(messages: list[Message]) -> tuple[str | None, list[Message]]:
    """Separate leading/standalone system messages from the conversation.

    Several providers (Anthropic among them) take the system prompt as a
    dedicated parameter rather than a message. This helper concatenates all
    ``SYSTEM`` messages and returns the remaining turns unchanged.
    """
    system_parts = [m.content for m in messages if m.role == Role.SYSTEM and m.content]
    rest = [m for m in messages if m.role != Role.SYSTEM]
    system = "\n\n".join(system_parts) if system_parts else None
    return system, rest


class ModelProvider(abc.ABC):
    """Abstract base class for all model providers."""

    #: Stable provider identifier, e.g. ``"anthropic"`` or ``"echo"``.
    name: str = "base"

    @abc.abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        **options: object,
    ) -> ModelResponse:
        """Generate a single completion.

        Parameters
        ----------
        messages:
            The conversation so far. System turns may be included here or passed
            via ``system``; providers should honour both.
        model:
            The concrete model id to call (already chosen by the router).
        tools:
            Tool schemas the model may call. ``None`` disables tool use.
        system:
            Optional explicit system prompt; merged with any system messages.
        max_tokens:
            Upper bound on output tokens.
        options:
            Provider-specific passthrough (e.g. ``thinking``).
        """
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release any underlying resources (HTTP clients, sockets). Optional."""
        return None
