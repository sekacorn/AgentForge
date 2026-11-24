"""A generic provider for any endpoint that speaks the Chat Completions API.

Many vendors and local servers expose an OpenAI-compatible endpoint. This one
provider reaches all of them: it is the existing OpenAI provider's request and
response mapping, pointed at a ``base_url`` taken from a named **preset** or
given directly. Vendor names live only in the preset data file, never in this
module, its class or the registry key (``openai_compatible``).

What this provider does not claim
---------------------------------

Reaching an endpoint through this class says nothing about whether a given
vendor's API works with it. Compatibility is a vendor's documentation claim;
the tests here run against a fake in-process server and no vendor endpoint is
ever contacted by this project.

Keys
----

The API key is read from an environment variable named by ``api_key_env`` (or
by the preset). It is never stored in configuration, and it never appears in an
exception, a log line or an event: errors raised here carry the endpoint's
origin and a status code, never the upstream message text, and anything that
does pass through is scrubbed of the key first.
"""

from __future__ import annotations

import os
from typing import Any

from forge.exceptions import (
    ConfigurationError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderResponseError,
)
from forge.models.endpoints import HOSTED
from forge.models.presets import ProviderBlock, ProviderPreset, block_for_endpoint, get_preset
from forge.models.providers.openai import OpenAIProvider
from forge.types import Message, ModelResponse, ToolSchema

#: Sent to a local server that needs no key. The SDK refuses an empty key, and
#: local servers ignore the header.
LOCAL_PLACEHOLDER_KEY = "not-needed"
REDACTED = "[redacted]"


class OpenAICompatibleProvider(OpenAIProvider):
    """Calls any OpenAI-compatible Chat Completions endpoint."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        preset: str | None = None,
        api_key: str | None = None,
        api_key_env: str | None = None,
        token_param: str | None = None,
        http_client: Any | None = None,
        presets_path: str | None = None,
    ) -> None:
        """Create the provider from a preset, a ``base_url``, or both.

        ``base_url`` overrides the preset's. ``api_key_env`` names the
        environment variable holding the key and overrides the preset's;
        ``api_key`` is for tests and programmatic callers. A hosted endpoint
        with no key is refused here rather than at the first request.
        ``presets_path`` names an operator's own preset file, loaded beside the
        built-in presets.
        """
        chosen: ProviderPreset | None = get_preset(preset, presets_path) if preset else None
        url = base_url or (chosen.base_url if chosen else None)
        if not url:
            raise ConfigurationError(
                "The openai_compatible provider needs a base_url or a preset.",
            )
        if not url.startswith(("http://", "https://")):
            raise ConfigurationError("The openai_compatible base_url must be an http(s) URL.")

        self.provider_block: ProviderBlock = block_for_endpoint(
            self.name, preset=chosen, base_url=base_url
        )
        env_name = api_key_env or (chosen.api_key_env if chosen else None)
        key = api_key or (os.environ.get(env_name) if env_name else None)
        if not key:
            if self.provider_block.hosting == HOSTED:
                raise ProviderAuthError(
                    "No API key for the hosted openai_compatible endpoint.",
                    context={
                        "endpoint": self.provider_block.endpoint_origin,
                        "api_key_env": env_name,
                    },
                )
            key = LOCAL_PLACEHOLDER_KEY
        self._secret = key
        self.api_key_env = env_name
        super().__init__(
            api_key=key,
            base_url=url,
            # A compatible endpoint is not OpenAI: a model merely named "o..."
            # is not a reasoning model there, so no prefix rule applies.
            token_param=token_param or (chosen.token_param if chosen else "max_tokens"),
            completion_token_model_prefixes=(),
            http_client=http_client,
        )

    def __repr__(self) -> str:
        block = self.provider_block
        return (
            f"OpenAICompatibleProvider(endpoint={block.endpoint_origin!r}, "
            f"preset={block.preset!r}, hosting={block.hosting!r})"
        )

    def _scrub(self, text: str) -> str:
        """Remove the API key from ``text`` wherever it appears."""
        if self._secret and self._secret != LOCAL_PLACEHOLDER_KEY:
            return text.replace(self._secret, REDACTED)
        return text

    def _context(self, model: str, exc: BaseException) -> dict[str, Any]:
        status = getattr(exc, "status_code", None)
        context: dict[str, Any] = {
            "model": model,
            "endpoint": self.provider_block.endpoint_origin,
            "error_type": type(exc).__name__,
        }
        if isinstance(status, int):
            context["status_code"] = status
        return context

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
        request = self._build_request(
            messages, model=model, tools=tools, system=system, max_tokens=max_tokens, **options
        )
        # The upstream message text is deliberately not carried into the error:
        # a server may echo request headers, and the cause chain is cut with
        # ``from None`` so the original exception cannot be rendered either.
        try:
            response = await self._client.chat.completions.create(**request)
        except self._openai.AuthenticationError as exc:
            raise ProviderAuthError(
                "Authentication failed at the openai_compatible endpoint.",
                context=self._context(model, exc),
            ) from None
        except self._openai.RateLimitError as exc:
            raise ProviderRateLimitError(
                "Rate limit hit at the openai_compatible endpoint.",
                context=self._context(model, exc),
            ) from None
        except self._openai.APIError as exc:
            raise ProviderResponseError(
                "The openai_compatible endpoint returned an error.",
                context=self._context(model, exc),
            ) from None
        return self._from_api_response(response, model)
