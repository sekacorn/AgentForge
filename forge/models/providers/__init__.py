"""Built-in model providers.

``EchoProvider`` is always available (offline, deterministic, free).
``AnthropicProvider`` is built in but requires the optional ``anthropic`` SDK
and an API key. Additional providers can be added by subclassing
:class:`~forge.models.base.ModelProvider` and registering them with the
orchestrator.
"""

from forge.models.providers.anthropic import AnthropicProvider
from forge.models.providers.echo import EchoProvider
from forge.models.providers.openai import OpenAIProvider

__all__ = ["AnthropicProvider", "EchoProvider", "OpenAIProvider"]
