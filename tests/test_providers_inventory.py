"""`forge providers inventory`: what it writes, and what it refuses to guess."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from tests.inventory_variants import (
    DECLARATIONS,
    OVERRIDE_VALUE,
    Named,
    compatible,
    declarations,
    ollama,
    variants,
)
from typer.testing import CliRunner

from forge.cli.main import app
from forge.exceptions import ConfigurationError
from forge.models.inventory import (
    MAX_DECLARATIONS_BYTES,
    build_inventory,
    load_declarations,
    parse_declarations,
    render_inventory,
)
from forge.orchestration.orchestrator import Orchestrator

PLANTED = "inv-planted-55aa-do-not-log"
runner = CliRunner()


def _schema() -> dict[str, Any]:
    text = files("forge.models").joinpath("data", "provider-declarations.schema.json")
    loaded: dict[str, Any] = json.loads(text.read_text(encoding="utf-8"))
    return loaded


def _decl(**providers: Any) -> bytes:
    return json.dumps(
        {"schema_version": "provider-declarations.v1", "providers": providers}
    ).encode("utf-8")


PLAIN = {"tool_calling": False, "streaming": False, "vendor_only": []}


# --- the declarations format ------------------------------------------------------


def test_the_declarations_schema_is_valid_and_every_fixture_matches_it() -> None:
    schema = _schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    fixtures = sorted(DECLARATIONS.glob("*.json"))
    assert [path.name for path in fixtures] == [
        "hosted-with-fallback.json",
        "local-only.json",
        "registry-models.json",
    ]
    for path in fixtures:
        document = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(document)
        assert parse_declarations(path.read_bytes())


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": "provider-declarations.v2", "providers": {"ollama": PLAIN}},
        {"schema_version": "provider-declarations.v1", "providers": {}},
        {"schema_version": "provider-declarations.v1", "providers": {"Ollama": PLAIN}},
        {"schema_version": "provider-declarations.v1", "providers": {"ollama": PLAIN}, "x": 1},
        {
            "schema_version": "provider-declarations.v1",
            "providers": {"ollama": {"streaming": False}},
        },
        {
            "schema_version": "provider-declarations.v1",
            "providers": {"ollama": {**PLAIN, "notes": PLANTED}},
        },
        {
            "schema_version": "provider-declarations.v1",
            "providers": {"ollama": {**PLAIN, "tool_calling": "yes"}},
        },
        {
            "schema_version": "provider-declarations.v1",
            "providers": {
                "ollama": {**PLAIN, "vendor_only": [{"feature": "x", "reconstruction": "maybe"}]}
            },
        },
        {
            "schema_version": "provider-declarations.v1",
            "providers": {"ollama": {**PLAIN, "models": []}},
        },
        {
            "schema_version": "provider-declarations.v1",
            "providers": {"ollama": {**PLAIN, "jurisdiction": "Earth"}},
        },
        {
            "schema_version": "provider-declarations.v1",
            "providers": {
                "ollama": {**PLAIN, "fallback": {"available": False, "provider_id": "x"}}
            },
        },
    ],
)
def test_malformed_declarations_are_refused_by_both_reader_and_schema(document: Any) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        parse_declarations(json.dumps(document).encode("utf-8"))
    assert PLANTED not in str(excinfo.value)
    assert not jsonschema.Draft202012Validator(_schema()).is_valid(document)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"{",
        b"\xff\xfe",
        b'{"schema_version": "provider-declarations.v1", "providers": {"a": {}, "a": {}}}',
        b'{"schema_version": "provider-declarations.v1", "providers": NaN}',
        b" " * (MAX_DECLARATIONS_BYTES + 1),
    ],
)
def test_unreadable_declarations_are_refused(data: bytes) -> None:
    with pytest.raises(ConfigurationError, match="provider declarations"):
        parse_declarations(data)


def test_duplicates_the_schema_cannot_see_are_refused() -> None:
    twice = {**PLAIN, "models": ["m", "m"]}
    with pytest.raises(ConfigurationError, match="twice"):
        parse_declarations(_decl(ollama=twice))
    feature = {"feature": "f", "reconstruction": "none"}
    with pytest.raises(ConfigurationError, match="twice"):
        parse_declarations(_decl(ollama={**PLAIN, "vendor_only": [feature, feature]}))


def test_a_missing_or_linked_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="does not exist"):
        load_declarations(tmp_path / "missing.json")
    target = tmp_path / "real.json"
    target.write_bytes(declarations("local-only.json"))
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are not available here")
    with pytest.raises(ConfigurationError, match="not a regular file"):
        load_declarations(link)


# --- what the inventory says ------------------------------------------------------


def test_the_local_only_inventory() -> None:
    document = variants()["local-only"].document
    (provider,) = document["providers"]

    assert document["schema_version"] == "provider-inventory.v1"
    assert document["generated_by"]["tool"] == "agentforge-oss"
    assert provider["provider_id"] == provider["kind"] == "ollama"
    assert provider["endpoint_origin"] == "http://127.0.0.1:11434"
    assert provider["jurisdiction"] == "not_applicable"
    assert provider["models"] == [{"model": "open-model-8b"}]
    assert provider["features"] == {"tool_calling": True, "streaming": True, "vendor_only": []}
    assert "hosting_declared" not in provider


def test_echo_is_left_out_unless_declared() -> None:
    assert [p["kind"] for p in variants()["local-only"].document["providers"]] == ["ollama"]
    (echo,) = variants()["echo-declared"].document["providers"]
    assert (echo["kind"], echo["endpoint_origin"], echo["jurisdiction"]) == (
        "echo",
        None,
        "not_applicable",
    )


def test_undeclared_models_are_refused_by_default() -> None:
    with pytest.raises(ConfigurationError, match="--use-registry-models"):
        build_inventory(
            {"ollama": ollama()},
            parse_declarations(declarations("registry-models.json")),
            environ={},
        )


def test_registry_models_are_opt_in_marked_and_the_command_says_so() -> None:
    result = variants()["registry-models"]
    models = result.document["providers"][0]["models"]

    assert models
    assert all(set(entry) == {"model", "source"} for entry in models)
    assert all(entry["source"] == "registry" for entry in models)
    assert result.notes == (
        "models for ollama were taken from the model registry and are marked "
        "from registry, not confirmed in use",
    )


def test_declared_models_carry_no_source_marker() -> None:
    # Emitted only when it carries information: a declared model is the default.
    for name, result in variants().items():
        if name == "registry-models":
            continue
        for provider in result.document["providers"]:
            assert all(set(entry) == {"model"} for entry in provider["models"]), name
    assert variants()["local-only"].notes == ()


def test_the_flag_does_not_override_declared_models() -> None:
    result = build_inventory(
        {"ollama": ollama()},
        parse_declarations(_decl(ollama={**PLAIN, "models": ["open-model-8b"]})),
        use_registry_models=True,
        environ={},
    )
    assert result.document["providers"][0]["models"] == [{"model": "open-model-8b"}]
    assert result.notes == ()


def test_a_provider_with_no_registry_models_must_declare_them() -> None:
    with pytest.raises(ConfigurationError, match="declare the models in use"):
        build_inventory(
            {"openai_compatible": compatible("http://127.0.0.1:8000/v1")},
            parse_declarations(_decl(openai_compatible=PLAIN)),
            use_registry_models=True,
            environ={},
        )


# --- SDK endpoint overrides: present, not reflected ---------------------------------


def test_an_sdk_endpoint_override_is_recorded_as_present_not_reflected() -> None:
    result = variants()["sdk-default-with-override"]
    providers = {p["provider_id"]: p for p in result.document["providers"]}

    assert providers["openai"]["endpoint_override_present"] is True
    assert providers["openai"]["endpoint_origin"] is None
    assert "endpoint_override_present" not in providers["anthropic"]
    assert result.notes == (
        "an endpoint override is set for openai and is not reflected; "
        "the inventory records the SDK default",
    )
    assert OVERRIDE_VALUE not in render_inventory(result)
    assert "proxy" not in " ".join(result.notes)


def test_without_an_override_the_marker_is_absent() -> None:
    providers = variants()["sdk-default"].document["providers"]
    assert all("endpoint_override_present" not in p for p in providers)


@pytest.mark.parametrize(
    ("variable", "provider"),
    [("OPENAI_BASE_URL", "openai"), ("ANTHROPIC_BASE_URL", "anthropic")],
)
def test_each_sdk_reads_its_own_variable(variable: str, provider: str) -> None:
    result = build_inventory(
        {provider: Named(provider)},
        parse_declarations(_decl(**{provider: {**PLAIN, "models": ["m"]}})),
        environ={variable: OVERRIDE_VALUE},
    )
    assert result.document["providers"][0]["endpoint_override_present"] is True


def test_a_blank_override_or_another_providers_variable_is_not_an_override() -> None:
    for environ in ({"OPENAI_BASE_URL": "  "}, {"ANTHROPIC_BASE_URL": OVERRIDE_VALUE}):
        result = build_inventory(
            {"openai": Named("openai")},
            parse_declarations(_decl(openai={**PLAIN, "models": ["m"]})),
            environ=environ,
        )
        assert "endpoint_override_present" not in result.document["providers"][0]


def test_a_provider_with_its_own_origin_never_records_an_override() -> None:
    result = build_inventory(
        {"openai_compatible": compatible("https://llm.vendor-a.example/v1")},
        parse_declarations(_decl(openai_compatible={**PLAIN, "models": ["m"]})),
        environ={"OPENAI_BASE_URL": OVERRIDE_VALUE},
    )
    assert "endpoint_override_present" not in result.document["providers"][0]


def test_hosted_with_a_fallback_and_a_vendor_only_feature() -> None:
    providers = {
        p["provider_id"]: p for p in variants()["hosted-with-fallback"].document["providers"]
    }
    hosted = providers["openai_compatible"]

    assert hosted["endpoint_origin"] == "https://llm.vendor-a.example"
    assert hosted["jurisdiction"] == "XA"
    assert hosted["openai_compatible_fallback"] == {"available": True, "provider_id": "ollama"}
    assert hosted["features"]["vendor_only"] == [
        {"feature": "server_side_prompt_cache", "reconstruction": "documented"}
    ]


def test_an_sdk_default_endpoint_is_null_and_jurisdiction_never_inferred() -> None:
    providers = {p["provider_id"]: p for p in variants()["sdk-default"].document["providers"]}
    assert providers["anthropic"]["endpoint_origin"] is None
    assert providers["anthropic"]["jurisdiction"] == "unknown"
    assert providers["openai"]["jurisdiction"] == "XC"


def test_a_documentation_range_endpoint_is_never_called_local() -> None:
    (provider,) = variants()["documentation-range-is-hosted"].document["providers"]
    assert provider["endpoint_origin"] == "http://203.0.113.7:8000"
    assert provider["jurisdiction"] == "unknown"


def test_only_the_origin_of_an_endpoint_is_written() -> None:
    text = "".join(render_inventory(result) for result in variants().values())
    assert "/v1" not in text
    assert "@" not in text
    assert "?" not in text


def test_a_preset_that_claims_local_at_a_public_host_is_not_written_as_not_applicable() -> None:
    from tests.inventory_variants import Described

    from forge.models.presets import ProviderBlock

    block = ProviderBlock(
        provider="openai_compatible",
        hosting="local",
        jurisdiction="not_applicable",
        endpoint_origin="https://llm.vendor-a.example",
    )
    result = build_inventory(
        {"openai_compatible": Described("openai_compatible", block)},
        parse_declarations(_decl(openai_compatible={**PLAIN, "models": ["m"]})),
    )
    assert result.document["providers"][0]["jurisdiction"] == "unknown"


def test_a_known_hosted_provider_at_a_local_endpoint_is_declared_hosted() -> None:
    from tests.inventory_variants import Described

    from forge.models.presets import ProviderBlock

    block = ProviderBlock(
        provider="openai_compatible",
        hosting="hosted",
        jurisdiction="XA",
        endpoint_origin="http://127.0.0.1:8443",
    )
    result = build_inventory(
        {"openai_compatible": Described("openai_compatible", block)},
        parse_declarations(_decl(openai_compatible={**PLAIN, "models": ["m"]})),
    )
    assert result.document["providers"][0]["hosting_declared"] == "hosted"


# --- what it refuses ---------------------------------------------------------------


def test_a_configured_provider_without_a_declaration_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="missing: ollama"):
        build_inventory(
            {"anthropic": Named("anthropic"), "ollama": ollama()},
            parse_declarations(_decl(anthropic={**PLAIN, "models": ["m"]})),
        )


def test_a_declaration_for_a_provider_that_is_not_configured_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="not configured"):
        build_inventory({"ollama": ollama()}, parse_declarations(_decl(ollama=PLAIN, openai=PLAIN)))


def test_only_echo_configured_refuses_a_declaration_for_a_real_provider() -> None:
    with pytest.raises(ConfigurationError, match="not configured"):
        build_inventory({"echo": Named("echo")}, parse_declarations(_decl(ollama=PLAIN)))


def test_a_fallback_must_name_another_provider_in_the_inventory() -> None:
    bad = {**PLAIN, "models": ["m"], "fallback": {"available": True, "provider_id": "nobody"}}
    with pytest.raises(ConfigurationError, match="fallback must name another provider"):
        build_inventory({"ollama": ollama()}, parse_declarations(_decl(ollama=bad)))
    itself = {**PLAIN, "models": ["m"], "fallback": {"available": True, "provider_id": "ollama"}}
    with pytest.raises(ConfigurationError, match="fallback must name another provider"):
        build_inventory({"ollama": ollama()}, parse_declarations(_decl(ollama=itself)))


def test_a_jurisdiction_that_contradicts_a_preset_or_a_local_provider_is_refused() -> None:
    from tests.inventory_variants import Described

    from forge.models.presets import ProviderBlock

    block = ProviderBlock(
        provider="openai_compatible",
        hosting="hosted",
        jurisdiction="XA",
        endpoint_origin="https://llm.vendor-a.example",
    )
    providers = {"openai_compatible": Described("openai_compatible", block)}
    with pytest.raises(ConfigurationError, match="contradicts"):
        build_inventory(
            providers,
            parse_declarations(
                _decl(openai_compatible={**PLAIN, "models": ["m"], "jurisdiction": "XB"})
            ),
        )
    withheld = build_inventory(
        providers,
        parse_declarations(
            _decl(openai_compatible={**PLAIN, "models": ["m"], "jurisdiction": "unknown"})
        ),
    )
    assert withheld.document["providers"][0]["jurisdiction"] == "unknown"
    with pytest.raises(ConfigurationError, match="local"):
        build_inventory(
            {"ollama": ollama()},
            parse_declarations(_decl(ollama={**PLAIN, "models": ["m"], "jurisdiction": "XA"})),
        )


def test_an_endpoint_that_cannot_be_reduced_to_an_origin_is_refused() -> None:
    from tests.inventory_variants import Described

    from forge.models.presets import ProviderBlock

    block = ProviderBlock(
        provider="openai_compatible",
        hosting="unknown",
        jurisdiction="unknown",
        endpoint_origin="unknown",
    )
    with pytest.raises(ConfigurationError, match="cannot be reduced to an origin"):
        build_inventory(
            {"openai_compatible": Described("openai_compatible", block)},
            parse_declarations(_decl(openai_compatible={**PLAIN, "models": ["m"]})),
        )


def test_a_provider_kind_the_inventory_cannot_record_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="not a kind"):
        build_inventory(
            {"custom": Named("custom")},
            parse_declarations(_decl(custom={**PLAIN, "models": ["m"]})),
        )


# --- determinism, the command, and the configured providers ------------------------


def test_rendering_is_deterministic() -> None:
    first = {name: render_inventory(result) for name, result in variants().items()}
    second = {name: render_inventory(result) for name, result in variants().items()}
    assert first == second
    assert all(text.endswith("\n") for text in first.values())


def _patch_providers(monkeypatch: pytest.MonkeyPatch, providers: dict[str, Any]) -> None:
    monkeypatch.setattr(
        Orchestrator, "configured_providers", classmethod(lambda cls, config: providers)
    )


def test_the_command_prints_plain_json_and_notes_go_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_providers(monkeypatch, {"echo": Named("echo"), "ollama": ollama()})
    path = tmp_path / "decl.json"
    path.write_bytes(declarations("registry-models.json"))
    result = runner.invoke(
        app,
        ["providers", "inventory", "--declarations", str(path), "--use-registry-models"],
    )

    assert result.exit_code == 0, result.output
    provider = json.loads(result.stdout)["providers"][0]
    assert provider["kind"] == "ollama"
    assert {entry["source"] for entry in provider["models"]} == {"registry"}
    assert "model registry" in result.stderr


def test_the_command_refuses_undeclared_models_without_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_providers(monkeypatch, {"ollama": ollama()})
    path = tmp_path / "decl.json"
    path.write_bytes(declarations("registry-models.json"))
    result = runner.invoke(app, ["providers", "inventory", "--declarations", str(path)])

    assert result.exit_code == 2
    assert "--use-registry-models" in result.output


def test_the_command_records_an_override_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_providers(monkeypatch, {"openai": Named("openai")})
    monkeypatch.setenv("OPENAI_BASE_URL", OVERRIDE_VALUE)
    path = tmp_path / "decl.json"
    path.write_bytes(_decl(openai={**PLAIN, "models": ["m"]}))
    result = runner.invoke(app, ["providers", "inventory", "--declarations", str(path)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["providers"][0]["endpoint_override_present"] is True
    assert OVERRIDE_VALUE not in result.output


def test_the_command_writes_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_providers(monkeypatch, {"ollama": ollama()})
    path = tmp_path / "decl.json"
    path.write_bytes(declarations("local-only.json"))
    out = tmp_path / "evidence" / "inventory.json"
    result = runner.invoke(
        app, ["providers", "inventory", "--declarations", str(path), "--output", str(out)]
    )

    assert result.exit_code == 0, result.output
    assert out.read_bytes() == render_inventory(variants()["local-only"]).encode("utf-8")


def test_the_command_exits_2_on_a_refusal_without_repeating_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_providers(monkeypatch, {"ollama": ollama()})
    path = tmp_path / "decl.json"
    path.write_bytes(_decl(ollama={**PLAIN, "notes": PLANTED}))
    result = runner.invoke(app, ["providers", "inventory", "--declarations", str(path)])

    assert result.exit_code == 2
    assert PLANTED not in result.output


def test_configured_providers_uses_the_default_build_and_contacts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from forge.config import ForgeConfig

    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FORGE_BEDROCK_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(Orchestrator, "_aws_credentials_available", lambda self: False)
    providers = Orchestrator.configured_providers(ForgeConfig.load())

    assert set(providers) >= {"echo", "ollama"}
    assert "anthropic" not in providers
