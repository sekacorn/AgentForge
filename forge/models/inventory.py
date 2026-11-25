"""Writing ``provider-inventory.v1`` from the providers Forge is configured with.

The AI Infrastructure Suite scores whether a system could leave its model
providers from a provider inventory. This module writes one for a Forge
deployment, so the inventory describes what is actually configured rather
than what someone remembers.

What comes from where:

- **Which providers**: the ones a default run would build from this
  configuration (``Orchestrator.configured_providers``). The offline ``echo``
  provider is a test double and is left out unless the operator declares it.
- **Endpoint origin**: :func:`~forge.models.presets.describe_provider`, which
  keeps scheme, host and port only. Credentials, path and query never appear.
- **Hosting**: never written as a claim of locality. The suite derives
  hosting from the origin itself; a provider this package knows to be hosted
  at a local-looking endpoint is declared ``hosted``, which may only add
  hosted-ness.
- **Jurisdiction**: a preset's stated value (attested), the operator's
  declaration, or ``"unknown"``. Never inferred.
- **Features in use, vendor-only features and reconstruction paths, models,
  fallbacks**: operator-supplied in a ``provider-declarations.v1`` file and
  never guessed. A configured provider without a declaration is refused, and
  so is a declaration for a provider that is not configured.
- **Models in use**: declared. A provider whose models are not declared is
  refused unless the operator opts in with ``use_registry_models``
  (``--use-registry-models``); its models are then this package's registry
  entries for that provider, each marked ``"source": "registry"``: from
  registry, not confirmed in use. The command says when it does this.
- **Endpoint overrides**: an SDK-default provider (``openai``, ``anthropic``)
  whose SDK would read an endpoint override from the environment
  (``OPENAI_BASE_URL``, ``ANTHROPIC_BASE_URL``) is recorded with
  ``"endpoint_override_present": true`` when that variable is set. The
  override is present, not reflected: the origin stays null, so the suite
  keeps treating the provider as the vendor's hosted default. The variable's
  value is never read into the document.

Both markers are written only when they carry information, so an inventory
that uses neither stays readable by suite 0.2.0b1; one that uses either needs
suite 0.2.0b2 or later.

Errors never repeat a value from the declarations file or the configuration.
"""

from __future__ import annotations

import json
import re
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from forge._version import __version__
from forge.exceptions import ConfigurationError
from forge.models.endpoints import HOSTED, UNKNOWN, endpoint_is_local
from forge.models.presets import NOT_APPLICABLE, describe_provider
from forge.models.registry import ModelRegistry

INVENTORY_SCHEMA_VERSION: Final = "provider-inventory.v1"
DECLARATIONS_SCHEMA_VERSION: Final = "provider-declarations.v1"
DECLARATIONS_SCHEMA_FILENAME: Final = "provider-declarations.schema.json"
MAX_DECLARATIONS_BYTES: Final = 64 * 1024
MAX_PROVIDERS: Final = 64
MAX_MODELS: Final = 64
MAX_VENDOR_ONLY: Final = 16
MAX_TEXT: Final = 256
OFFLINE_PROVIDERS: Final = frozenset({"echo"})
#: Provider names a ``provider-inventory.v1`` accepts as a ``kind``.
INVENTORY_KINDS: Final = frozenset(
    {"openai", "openai_compatible", "anthropic", "bedrock", "ollama", "echo"}
)

_ID: Final = re.compile(r"[a-z0-9_]{1,64}")
_JURISDICTION: Final = re.compile(r"[A-Z]{2}")
_ORIGIN: Final = re.compile(r"https?://(\[[0-9a-f:.]+\]|[a-z0-9.-]+)(:[0-9]{1,5})?")
_DECLARATION_KEYS: Final = frozenset(
    {"tool_calling", "streaming", "vendor_only", "models", "jurisdiction", "fallback"}
)
_REQUIRED_KEYS: Final = frozenset({"tool_calling", "streaming", "vendor_only"})
#: The environment variable each SDK-default provider's SDK reads for an
#: endpoint override. Present, not reflected: only its presence is recorded.
SDK_ENDPOINT_OVERRIDE_ENV: Final = {
    "openai": "OPENAI_BASE_URL",
    "anthropic": "ANTHROPIC_BASE_URL",
}
REGISTRY_SOURCE: Final = "registry"


@dataclass(frozen=True)
class Declaration:
    """What the operator states about one provider. All of it is attested."""

    tool_calling: bool
    streaming: bool
    vendor_only: tuple[tuple[str, str], ...]
    models: tuple[str, ...] | None = None
    jurisdiction: str | None = None
    fallback_available: bool = False
    fallback_provider: str | None = None


@dataclass(frozen=True)
class InventoryResult:
    """The inventory document, and what a reader should be told about it."""

    document: dict[str, Any]
    notes: tuple[str, ...] = field(default_factory=tuple)


# --- declarations ----------------------------------------------------------------


def _fail(message: str) -> ConfigurationError:
    return ConfigurationError(f"provider declarations: {message}")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _fail("a key appears twice in one object.")
        result[key] = value
    return result


def _reject_constant(_literal: str) -> Any:
    raise _fail("NaN and Infinity are not allowed.")


def _flag(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(f"{where} must be true or false.")
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise _fail(f"{where} must be a non-empty string of at most {MAX_TEXT} characters.")
    return value


def _declaration(name: str, raw: object) -> Declaration:
    where = f"provider {name}"
    if not isinstance(raw, dict):
        raise _fail(f"{where} must be an object.")
    keys = set(raw)
    if not keys >= _REQUIRED_KEYS:
        raise _fail(f"{where} must declare tool_calling, streaming and vendor_only.")
    if keys - _DECLARATION_KEYS:
        raise _fail(f"{where} has a field this format does not define.")
    vendor_only_raw = raw["vendor_only"]
    if not isinstance(vendor_only_raw, list) or len(vendor_only_raw) > MAX_VENDOR_ONLY:
        raise _fail(f"{where} vendor_only must be a list of at most {MAX_VENDOR_ONLY}.")
    vendor_only: list[tuple[str, str]] = []
    for item in vendor_only_raw:
        if not isinstance(item, dict) or set(item) != {"feature", "reconstruction"}:
            raise _fail(f"{where} vendor_only entries take feature and reconstruction.")
        feature, reconstruction = item["feature"], item["reconstruction"]
        if not isinstance(feature, str) or not _ID.fullmatch(feature):
            raise _fail(f"{where} vendor_only feature must match [a-z0-9_]{{1,64}}.")
        if reconstruction not in ("documented", "none"):
            raise _fail(f"{where} reconstruction must be documented or none.")
        vendor_only.append((feature, reconstruction))
    if len({feature for feature, _ in vendor_only}) != len(vendor_only):
        raise _fail(f"{where} lists a vendor-only feature twice.")
    models: tuple[str, ...] | None = None
    if "models" in raw:
        listed = raw["models"]
        if not isinstance(listed, list) or not listed or len(listed) > MAX_MODELS:
            raise _fail(f"{where} models must be a non-empty list of at most {MAX_MODELS}.")
        models = tuple(_text(model, f"{where} model") for model in listed)
        if len(set(models)) != len(models):
            raise _fail(f"{where} lists a model twice.")
    jurisdiction: str | None = None
    if "jurisdiction" in raw:
        value = raw["jurisdiction"]
        if not isinstance(value, str) or not (_JURISDICTION.fullmatch(value) or value == UNKNOWN):
            raise _fail(f"{where} jurisdiction must be two capital letters or unknown.")
        jurisdiction = value
    fallback_available, fallback_provider = False, None
    if "fallback" in raw:
        fallback = raw["fallback"]
        if not isinstance(fallback, dict) or "available" not in fallback:
            raise _fail(f"{where} fallback must be an object with available.")
        if set(fallback) - {"available", "provider_id"}:
            raise _fail(f"{where} fallback has a field this format does not define.")
        fallback_available = _flag(fallback["available"], f"{where} fallback available")
        if "provider_id" in fallback:
            target = fallback["provider_id"]
            if not fallback_available or not isinstance(target, str) or not _ID.fullmatch(target):
                raise _fail(f"{where} fallback provider_id needs available: true and a valid id.")
            fallback_provider = target
    return Declaration(
        tool_calling=_flag(raw["tool_calling"], f"{where} tool_calling"),
        streaming=_flag(raw["streaming"], f"{where} streaming"),
        vendor_only=tuple(vendor_only),
        models=models,
        jurisdiction=jurisdiction,
        fallback_available=fallback_available,
        fallback_provider=fallback_provider,
    )


def parse_declarations(data: bytes) -> dict[str, Declaration]:
    """Validate ``provider-declarations.v1`` bytes."""
    if len(data) > MAX_DECLARATIONS_BYTES:
        raise _fail("the file is too large.")
    try:
        document = json.loads(
            data.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _fail("the file is not well-formed UTF-8 JSON.") from None
    if not isinstance(document, dict) or set(document) != {"schema_version", "providers"}:
        raise _fail("expected exactly schema_version and providers.")
    if document["schema_version"] != DECLARATIONS_SCHEMA_VERSION:
        raise _fail(f"expected a {DECLARATIONS_SCHEMA_VERSION} document.")
    providers = document["providers"]
    if not isinstance(providers, dict) or not providers or len(providers) > MAX_PROVIDERS:
        raise _fail(f"providers must name between 1 and {MAX_PROVIDERS} providers.")
    for name in providers:
        if not _ID.fullmatch(name):
            raise _fail("a provider name must match [a-z0-9_]{1,64}.")
    return {name: _declaration(name, raw) for name, raw in providers.items()}


def load_declarations(path: str | Path) -> dict[str, Declaration]:
    """Read a bounded ``provider-declarations.v1`` file."""
    file = Path(path)
    try:
        if file.is_symlink() or not file.is_file():
            raise _fail("the file does not exist or is not a regular file.")
        if file.stat().st_size > MAX_DECLARATIONS_BYTES:
            raise _fail("the file is too large.")
        data = file.read_bytes()
    except OSError:
        raise _fail("the file could not be read.") from None
    return parse_declarations(data)


# --- the inventory ---------------------------------------------------------------


def _jurisdiction(block_value: str, declared: str | None, name: str) -> str:
    """A preset's attested value, the operator's declaration, or unknown."""
    if declared is None or declared == block_value:
        return block_value
    if block_value == NOT_APPLICABLE:
        raise ConfigurationError(
            f"provider {name} is local, so a declared jurisdiction does not apply."
        )
    if block_value == UNKNOWN or declared == UNKNOWN:
        # Filling an unknown, or withholding a preset's value: both are the
        # operator's statement, and neither is weaker than what was there.
        return declared
    raise ConfigurationError(
        f"provider {name}: the declared jurisdiction contradicts the preset's stated one."
    )


def _entry(
    name: str,
    provider: object,
    declaration: Declaration,
    registry: ModelRegistry,
    *,
    use_registry_models: bool,
    environ: Mapping[str, str],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    if name not in INVENTORY_KINDS:
        raise ConfigurationError(f"provider {name} is not a kind provider-inventory.v1 can record.")
    block = describe_provider(provider)
    origin = block.endpoint_origin
    if origin is not None and (origin == UNKNOWN or not _ORIGIN.fullmatch(origin)):
        raise ConfigurationError(f"provider {name}: its endpoint cannot be reduced to an origin.")
    if origin is None and name in ("ollama", "openai_compatible"):
        raise ConfigurationError(f"provider {name} has no endpoint to record.")
    notes: list[str] = []
    model_entries: list[dict[str, str]]
    if declaration.models is not None:
        model_entries = [{"model": model} for model in declaration.models]
    elif not use_registry_models:
        raise ConfigurationError(
            f"provider {name}: declare the models in use, or pass --use-registry-models "
            "to take them from the model registry, marked as not confirmed in use."
        )
    else:
        models = tuple(info.name for info in registry.all() if info.provider == name)
        if not models:
            raise ConfigurationError(
                f"provider {name}: declare the models in use; the registry lists none for it."
            )
        model_entries = [{"model": model, "source": REGISTRY_SOURCE} for model in models]
        notes.append(
            f"models for {name} were taken from the model registry and are marked "
            "from registry, not confirmed in use"
        )
    override_variable = SDK_ENDPOINT_OVERRIDE_ENV.get(name)
    override = (
        origin is None
        and override_variable is not None
        and bool(environ.get(override_variable, "").strip())
    )
    if override:
        notes.append(
            f"an endpoint override is set for {name} and is not reflected; "
            "the inventory records the SDK default"
        )
    jurisdiction = _jurisdiction(block.jurisdiction, declaration.jurisdiction, name)
    if (
        jurisdiction == NOT_APPLICABLE
        and name not in OFFLINE_PROVIDERS
        and (origin is None or not endpoint_is_local(origin))
    ):
        # A preset can call itself local; the suite derives hosting from the
        # origin and refuses not_applicable for anything it finds hosted.
        jurisdiction = UNKNOWN
    entry: dict[str, Any] = {
        "provider_id": name,
        "kind": name,
        "preset": block.preset,
        "preset_source": block.preset_source if block.preset is not None else None,
        "endpoint_origin": origin,
        **({"endpoint_override_present": True} if override else {}),
        "jurisdiction": jurisdiction,
        "models": model_entries,
        "openai_compatible_fallback": {"available": declaration.fallback_available},
        "features": {
            "tool_calling": declaration.tool_calling,
            "streaming": declaration.streaming,
            "vendor_only": [
                {"feature": feature, "reconstruction": reconstruction}
                for feature, reconstruction in declaration.vendor_only
            ],
        },
    }
    if declaration.fallback_provider is not None:
        entry["openai_compatible_fallback"]["provider_id"] = declaration.fallback_provider
    if block.hosting == HOSTED and origin is not None and endpoint_is_local(origin):
        # Known hosted at a local-looking endpoint: a declaration may only add
        # hosted-ness, so say so rather than let it read as local.
        entry["hosting_declared"] = HOSTED
    return entry, tuple(notes)


def build_inventory(
    providers: Mapping[str, object],
    declarations: Mapping[str, Declaration],
    *,
    registry: ModelRegistry | None = None,
    use_registry_models: bool = False,
    environ: Mapping[str, str] | None = None,
) -> InventoryResult:
    """Describe the configured providers as a ``provider-inventory.v1`` document.

    ``use_registry_models`` lets a provider without declared models take them
    from the registry, marked as not confirmed in use; without it such a
    provider is refused. ``environ`` is where SDK endpoint overrides are looked
    for, ``os.environ`` by default.
    """
    registry = registry or ModelRegistry()
    environ = os.environ if environ is None else environ
    included = [name for name in providers if name not in OFFLINE_PROVIDERS or name in declarations]
    undeclared = sorted(name for name in included if name not in declarations)
    if undeclared:
        raise ConfigurationError(
            "Declare features in use for every configured provider; missing: "
            + ", ".join(undeclared)
            + "."
        )
    unconfigured = sorted(name for name in declarations if name not in providers)
    if unconfigured:
        raise ConfigurationError(
            "These declared providers are not configured, or could not be built: "
            + ", ".join(unconfigured)
            + "."
        )
    entries: list[dict[str, Any]] = []
    notes: list[str] = []
    for name in sorted(included):
        entry, entry_notes = _entry(
            name,
            providers[name],
            declarations[name],
            registry,
            use_registry_models=use_registry_models,
            environ=environ,
        )
        entries.append(entry)
        notes.extend(entry_notes)
    ids = {entry["provider_id"] for entry in entries}
    for entry in entries:
        target = entry["openai_compatible_fallback"].get("provider_id")
        if target is not None and (target not in ids or target == entry["provider_id"]):
            raise ConfigurationError(
                f"provider {entry['provider_id']}: its fallback must name another provider "
                "in this inventory."
            )
    document = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "generated_by": {"tool": "agentforge-oss", "version": __version__},
        "providers": entries,
    }
    return InventoryResult(document=document, notes=tuple(notes))


def render_inventory(result: InventoryResult) -> str:
    """Deterministic JSON: fixed field order, two-space indent, trailing newline."""
    return json.dumps(result.document, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
