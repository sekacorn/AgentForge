"""Every kind of provider-inventory.v1 `forge providers inventory` can write.

Built from real provider objects (or the provider block a provider carries) and
the shipped declaration fixtures, so the unit tests and the suite-contract test
check exactly what the command produces. Hosts are placeholders.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from forge.models.inventory import InventoryResult, build_inventory, parse_declarations
from forge.models.presets import ProviderBlock, block_for_endpoint, get_preset
from forge.models.providers.ollama import OllamaProvider

DECLARATIONS: Final = Path(__file__).resolve().parent / "fixtures" / "provider-declarations"


class Described:
    """A stand-in provider that carries its own block, as built-in providers do."""

    def __init__(self, name: str, block: ProviderBlock) -> None:
        self.name = name
        self.provider_block = block


class Named:
    """A provider described only by its name, like the SDK-backed ones."""

    def __init__(self, name: str) -> None:
        self.name = name


def declarations(name: str) -> bytes:
    return (DECLARATIONS / name).read_bytes()


def ollama(base_url: str = "http://127.0.0.1:11434") -> OllamaProvider:
    return OllamaProvider(base_url=base_url)


def compatible(base_url: str | None = None, preset: str | None = None) -> Described:
    chosen = get_preset(preset) if preset else None
    return Described(
        "openai_compatible",
        block_for_endpoint("openai_compatible", preset=chosen, base_url=base_url),
    )


#: A placeholder override value. It must never appear in an inventory.
OVERRIDE_VALUE: Final = "https://proxy.vendor-c.example/v1"


def _build(providers: dict[str, Any], declared: dict[str, Any], **options: Any) -> InventoryResult:
    data = json.dumps({"schema_version": "provider-declarations.v1", "providers": declared})
    # An explicit, empty environment unless a variant sets one, so the CI
    # runner's own environment never changes what a variant writes.
    options.setdefault("environ", {})
    return build_inventory(providers, parse_declarations(data.encode("utf-8")), **options)


def variants() -> dict[str, InventoryResult]:
    """Name -> inventory, one per distinct path through the writer."""
    hosted_decl = json.loads(declarations("hosted-with-fallback.json"))["providers"]
    plain = {"tool_calling": False, "streaming": False, "vendor_only": []}
    return {
        "local-only": build_inventory(
            {"echo": Named("echo"), "ollama": ollama()},
            parse_declarations(declarations("local-only.json")),
            environ={},
        ),
        "registry-models": build_inventory(
            {"echo": Named("echo"), "ollama": ollama()},
            parse_declarations(declarations("registry-models.json")),
            use_registry_models=True,
            environ={},
        ),
        "hosted-with-fallback": _build(
            {
                "openai_compatible": compatible("https://llm.vendor-a.example/v1"),
                "ollama": ollama(),
            },
            hosted_decl,
        ),
        "sdk-default": _build(
            {"anthropic": Named("anthropic"), "openai": Named("openai")},
            {
                "anthropic": {**plain, "models": ["vendor-b-model"]},
                "openai": {**plain, "models": ["vendor-c-model"], "jurisdiction": "XC"},
            },
        ),
        "ipv6-private": _build(
            {"openai_compatible": compatible("http://[fd00::10]:8000/v1")},
            {"openai_compatible": {**plain, "models": ["open-model-8b"]}},
        ),
        "documentation-range-is-hosted": _build(
            {"openai_compatible": compatible("http://203.0.113.7:8000/v1")},
            {"openai_compatible": {**plain, "models": ["model-x"]}},
        ),
        "sdk-default-with-override": _build(
            {"anthropic": Named("anthropic"), "openai": Named("openai")},
            {
                "anthropic": {**plain, "models": ["vendor-b-model"]},
                "openai": {**plain, "models": ["vendor-c-model"]},
            },
            environ={"OPENAI_BASE_URL": OVERRIDE_VALUE},
        ),
        "echo-declared": _build(
            {"echo": Named("echo")},
            {"echo": {**plain, "models": ["echo-mini"]}},
        ),
    }
