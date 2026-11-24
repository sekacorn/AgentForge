"""Named presets for OpenAI-compatible endpoints, and the provider block.

A preset is **data**: where a vendor documents an OpenAI-compatible endpoint,
which environment variable conventionally holds its key, the vendor's stated
jurisdiction, and whether the endpoint is local or hosted. Nothing in a preset
asserts that the vendor's API was contacted or works. Vendor names appear only
as keys and URL strings in the data file, never in a module, class or CLI name.

This module also builds the **provider block**: the small record of provider,
hosting and jurisdiction that accompanies every model call. It is always
populated. Where nothing is known the value is the literal ``"unknown"``, never
an omission and never a guess, so a policy that denies unknown jurisdictions
has something to match.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any, Final

from forge.exceptions import ConfigurationError
from forge.models.endpoints import HOSTED, LOCAL, UNKNOWN, endpoint_origin, hosting_for

PRESETS_SCHEMA_VERSION: Final = "provider-presets.v1"
PRESETS_FILENAME: Final = "provider-presets.json"
NOT_APPLICABLE: Final = "not_applicable"
TOKEN_PARAMS: Final = ("max_tokens", "max_completion_tokens")

_PRESET_ID: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_JURISDICTION: Final = re.compile(r"[A-Z]{2}|not_applicable")
_ENV_NAME: Final = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
_PRESET_KEYS: Final = frozenset(
    {
        "base_url",
        "docs_url",
        "jurisdiction",
        "hosting",
        "api_key_env",
        "token_param",
        "compatibility",
        "review",
        "notes",
    }
)

#: Providers this package ships whose hosting is a fact of their construction.
#: Their jurisdiction is "unknown" here: no preset declares it, and this module
#: does not guess.
_BUILT_IN_HOSTING: Final = {
    "echo": LOCAL,
    "anthropic": HOSTED,
    "openai": HOSTED,
    "bedrock": HOSTED,
}


@dataclass(frozen=True)
class ProviderPreset:
    """One named OpenAI-compatible endpoint, as declared in the data file."""

    preset_id: str
    base_url: str
    docs_url: str
    jurisdiction: str
    hosting: str
    api_key_env: str | None
    token_param: str
    compatibility: str
    review: str
    notes: str


@dataclass(frozen=True)
class ProviderBlock:
    """Who served a model call, where, and under whose stated jurisdiction.

    ``jurisdiction`` is attested: it is the vendor's stated corporate
    jurisdiction from a preset, never something this package verified, and it
    says nothing about where a packet travels.
    """

    provider: str
    hosting: str
    jurisdiction: str
    preset: str | None = None
    endpoint_origin: str | None = None

    def to_event(self) -> dict[str, Any]:
        """Fields for a model-call event. Always carries all three core keys."""
        return {
            "hosting": self.hosting,
            "jurisdiction": self.jurisdiction,
            "provider_preset": self.preset,
        }

    def to_policy_request(self, model: str) -> dict[str, dict[str, Any]]:
        """The ``model`` and ``provider`` blocks of an AgentPolicyPack request."""
        return {
            "model": {"provider": self.provider, "name": model, "hosting": self.hosting},
            "provider": {
                "name": self.preset or self.provider,
                "hosting": self.hosting,
                "jurisdiction": self.jurisdiction,
            },
        }


def _preset(preset_id: str, raw: object) -> ProviderPreset:
    if not _PRESET_ID.fullmatch(preset_id):
        raise ConfigurationError(f"Invalid provider preset id: {preset_id!r}")
    if not isinstance(raw, dict) or set(raw) != _PRESET_KEYS:
        raise ConfigurationError(f"Provider preset {preset_id!r} has unexpected or missing fields.")
    base_url, docs_url = raw["base_url"], raw["docs_url"]
    if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
        raise ConfigurationError(f"Provider preset {preset_id!r} has an invalid base_url.")
    if not isinstance(docs_url, str) or not docs_url.startswith("https://"):
        raise ConfigurationError(f"Provider preset {preset_id!r} has an invalid docs_url.")
    jurisdiction = raw["jurisdiction"]
    if not isinstance(jurisdiction, str) or not _JURISDICTION.fullmatch(jurisdiction):
        raise ConfigurationError(f"Provider preset {preset_id!r} has an invalid jurisdiction.")
    hosting = raw["hosting"]
    if hosting not in (LOCAL, HOSTED):
        raise ConfigurationError(f"Provider preset {preset_id!r} has an invalid hosting value.")
    # The declaration must agree with the endpoint. A preset that says "local"
    # for a public URL would be a claim the URL itself contradicts.
    if hosting != hosting_for(base_url):
        raise ConfigurationError(
            f"Provider preset {preset_id!r} declares hosting {hosting!r}, "
            "which its base_url contradicts."
        )
    if (hosting == LOCAL) != (jurisdiction == NOT_APPLICABLE):
        raise ConfigurationError(
            f"Provider preset {preset_id!r}: only a local endpoint has no jurisdiction."
        )
    api_key_env = raw["api_key_env"]
    if api_key_env is not None and (
        not isinstance(api_key_env, str) or not _ENV_NAME.fullmatch(api_key_env)
    ):
        raise ConfigurationError(f"Provider preset {preset_id!r} has an invalid api_key_env.")
    if raw["token_param"] not in TOKEN_PARAMS:
        raise ConfigurationError(f"Provider preset {preset_id!r} has an invalid token_param.")
    if raw["compatibility"] not in ("documented", "partial"):
        raise ConfigurationError(f"Provider preset {preset_id!r} has an invalid compatibility.")
    if raw["review"] not in ("unreviewed", "reviewed"):
        raise ConfigurationError(f"Provider preset {preset_id!r} has an invalid review status.")
    if not isinstance(raw["notes"], str):
        raise ConfigurationError(f"Provider preset {preset_id!r} has invalid notes.")
    return ProviderPreset(
        preset_id=preset_id,
        base_url=base_url,
        docs_url=docs_url,
        jurisdiction=jurisdiction,
        hosting=hosting,
        api_key_env=api_key_env,
        token_param=raw["token_param"],
        compatibility=raw["compatibility"],
        review=raw["review"],
        notes=raw["notes"],
    )


def parse_presets(document: object) -> dict[str, ProviderPreset]:
    """Validate a ``provider-presets.v1`` document and return its presets."""
    if not isinstance(document, dict) or not {"schema_version", "presets"} <= set(document):
        raise ConfigurationError("Expected a provider-presets.v1 document.")
    if set(document) - {"schema_version", "presets", "notice"}:
        raise ConfigurationError("Unexpected fields in the provider presets document.")
    if document["schema_version"] != PRESETS_SCHEMA_VERSION:
        raise ConfigurationError(f"Expected schema_version {PRESETS_SCHEMA_VERSION!r}.")
    presets = document["presets"]
    if not isinstance(presets, dict) or not presets:
        raise ConfigurationError("The provider presets document declares no presets.")
    return {preset_id: _preset(preset_id, raw) for preset_id, raw in presets.items()}


def load_presets() -> dict[str, ProviderPreset]:
    """Load the presets shipped with this package."""
    text = resources.files("forge.models").joinpath("data", PRESETS_FILENAME).read_text("utf-8")
    return parse_presets(json.loads(text))


def get_preset(preset_id: str) -> ProviderPreset:
    """Return one shipped preset, or refuse with the list of known ids."""
    presets = load_presets()
    if preset_id not in presets:
        raise ConfigurationError(
            f"Unknown provider preset {preset_id!r}.",
            context={"known_presets": sorted(presets)},
        )
    return presets[preset_id]


def block_for_endpoint(
    provider: str, *, preset: ProviderPreset | None, base_url: str | None
) -> ProviderBlock:
    """Describe an OpenAI-compatible endpoint.

    With a preset and no override, the preset's declarations stand. With a
    custom ``base_url``, hosting is **derived from that URL** and the
    jurisdiction is ``"unknown"`` unless the URL is the preset's own: a preset
    describes its vendor's endpoint, not whatever the operator pointed it at.
    """
    if base_url is None and preset is None:
        return ProviderBlock(provider=provider, hosting=UNKNOWN, jurisdiction=UNKNOWN)
    if preset is not None and (base_url is None or base_url == preset.base_url):
        return ProviderBlock(
            provider=provider,
            hosting=preset.hosting,
            jurisdiction=preset.jurisdiction,
            preset=preset.preset_id,
            endpoint_origin=endpoint_origin(preset.base_url),
        )
    url = base_url or ""
    hosting = hosting_for(url)
    return ProviderBlock(
        provider=provider,
        hosting=hosting,
        jurisdiction=NOT_APPLICABLE if hosting == LOCAL else UNKNOWN,
        preset=None,
        endpoint_origin=endpoint_origin(url),
    )


def describe_provider(provider: object) -> ProviderBlock:
    """The provider block for any provider instance. Never raises, never omits.

    A provider that describes itself (``provider_block``) is believed. The
    built-in providers are described from what their construction makes true.
    Anything else is ``unknown`` on both counts: a custom provider this package
    knows nothing about is not assumed to be local.
    """
    name = str(getattr(provider, "name", UNKNOWN) or UNKNOWN)
    own = getattr(provider, "provider_block", None)
    if isinstance(own, ProviderBlock):
        return own
    if name == "ollama":
        base_url = getattr(provider, "_base_url", None)
        if isinstance(base_url, str) and base_url:
            hosting = hosting_for(base_url)
            return ProviderBlock(
                provider=name,
                hosting=hosting,
                jurisdiction=NOT_APPLICABLE if hosting == LOCAL else UNKNOWN,
                endpoint_origin=endpoint_origin(base_url),
            )
        return ProviderBlock(provider=name, hosting=UNKNOWN, jurisdiction=UNKNOWN)
    if name in _BUILT_IN_HOSTING:
        hosting = _BUILT_IN_HOSTING[name]
        return ProviderBlock(
            provider=name,
            hosting=hosting,
            jurisdiction=NOT_APPLICABLE if hosting == LOCAL else UNKNOWN,
        )
    return ProviderBlock(provider=name, hosting=UNKNOWN, jurisdiction=UNKNOWN)
