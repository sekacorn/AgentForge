"""Provider presets, endpoint classification, config wiring and the provider block.

Preset tests assert **data only**: that every preset carries a base URL, a
documentation link, a jurisdiction and a hosting value that agree with each
other. No test here contacts a vendor or claims a vendor's API works.
"""

from __future__ import annotations

import json
import logging
import sys
import types
from importlib import resources
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge import EventType, ForgeConfig, Orchestrator
from forge.exceptions import ConfigurationError
from forge.models.endpoints import endpoint_is_local, endpoint_origin, hosting_for
from forge.models.presets import (
    NOT_APPLICABLE,
    PRESETS_SCHEMA_VERSION,
    ProviderBlock,
    block_for_endpoint,
    describe_provider,
    get_preset,
    load_presets,
    parse_presets,
)

EXPECTED_PRESETS = {
    "mistral",
    "deepseek",
    "moonshot",
    "qwen",
    "groq",
    "together",
    "fireworks",
    "gemini_openai",
    "local_vllm",
    "local_lmstudio",
    "local_ollama_openai",
}


def _raw_document() -> dict:
    text = (
        resources.files("forge.models").joinpath("data", "provider-presets.json").read_text("utf-8")
    )
    return json.loads(text)


def _schema() -> dict:
    text = (
        resources.files("forge.models")
        .joinpath("data", "provider-presets.schema.json")
        .read_text("utf-8")
    )
    return json.loads(text)


# --- preset data ------------------------------------------------------------


def test_the_shipped_presets_are_the_approved_set() -> None:
    assert set(load_presets()) == EXPECTED_PRESETS


def test_the_data_file_is_versioned_and_ships_in_the_package() -> None:
    document = _raw_document()

    assert document["schema_version"] == PRESETS_SCHEMA_VERSION
    assert _schema()["properties"]["schema_version"]["const"] == PRESETS_SCHEMA_VERSION


@pytest.mark.parametrize("preset_id", sorted(EXPECTED_PRESETS))
def test_every_preset_records_the_four_required_facts(preset_id: str) -> None:
    preset = get_preset(preset_id)

    assert preset.base_url.startswith(("http://", "https://"))
    assert preset.docs_url.startswith("https://")
    assert preset.hosting in {"local", "hosted"}
    assert preset.jurisdiction == NOT_APPLICABLE or (
        len(preset.jurisdiction) == 2 and preset.jurisdiction.isupper()
    )


@pytest.mark.parametrize("preset_id", sorted(EXPECTED_PRESETS))
def test_a_presets_hosting_agrees_with_its_own_endpoint(preset_id: str) -> None:
    preset = get_preset(preset_id)

    assert preset.hosting == hosting_for(preset.base_url)
    assert (preset.hosting == "local") == (preset.jurisdiction == NOT_APPLICABLE)
    assert (preset.hosting == "local") == preset_id.startswith("local_")
    # A hosted preset names where its key lives; a local one needs none.
    assert (preset.api_key_env is None) == (preset.hosting == "local")


def test_partial_compatibility_is_recorded_with_a_reason() -> None:
    partial = {pid: p for pid, p in load_presets().items() if p.compatibility == "partial"}

    assert set(partial) == {"gemini_openai", "local_ollama_openai", "qwen"}
    assert all(preset.notes for preset in partial.values())


def test_the_data_file_matches_its_json_schema() -> None:
    document, schema = _raw_document(), _schema()
    preset_schema = schema["$defs"]["preset"]

    assert schema["additionalProperties"] is False
    assert preset_schema["additionalProperties"] is False
    for preset_id, raw in document["presets"].items():
        assert set(raw) == set(preset_schema["required"]), preset_id
        assert set(raw) <= set(preset_schema["properties"]), preset_id
        assert raw["hosting"] in preset_schema["properties"]["hosting"]["enum"]
        assert raw["token_param"] in preset_schema["properties"]["token_param"]["enum"]
        assert raw["compatibility"] in preset_schema["properties"]["compatibility"]["enum"]
        assert raw["review"] in preset_schema["properties"]["review"]["enum"]


def test_the_data_file_validates_with_a_json_schema_validator() -> None:
    # jsonschema is not a dependency of this package; the check above covers
    # the same contract by hand when it is absent.
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(_raw_document(), _schema())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"hosting": "hosted"}, "base_url contradicts"),
        ({"jurisdiction": "US"}, "only a local endpoint"),
        ({"jurisdiction": "usa"}, "invalid jurisdiction"),
        ({"base_url": "ftp://x"}, "invalid base_url"),
        ({"docs_url": "http://insecure.example"}, "invalid docs_url"),
        ({"token_param": "max_output"}, "invalid token_param"),
        ({"api_key_env": "lower_case"}, "invalid api_key_env"),
        ({"compatibility": "tested"}, "invalid compatibility"),
        ({"review": "verified"}, "invalid review"),
        ({"extra": 1}, "unexpected or missing"),
    ],
)
def test_a_malformed_preset_is_refused(mutation: dict, message: str) -> None:
    document = _raw_document()
    document["presets"] = {"local_vllm": {**document["presets"]["local_vllm"], **mutation}}

    with pytest.raises(ConfigurationError, match=message):
        parse_presets(document)


def test_another_schema_version_is_refused() -> None:
    document = _raw_document()
    document["schema_version"] = "provider-presets.v2"

    with pytest.raises(ConfigurationError, match="schema_version"):
        parse_presets(document)


# --- endpoints --------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000/v1",
        "http://127.0.0.1:1234",
        "http://[::1]:8000",
        "http://192.168.1.5",
        "http://10.1.2.3",
        "http://169.254.1.1",
        "http://0.0.0.0:11434",
    ],
)
def test_local_endpoints_are_recognised(url: str) -> None:
    assert endpoint_is_local(url)
    assert hosting_for(url) == "local"


@pytest.mark.parametrize(
    "url",
    ["https://api.vendor.example/v1", "https://8.8.8.8", "http://db.internal.corp", "", "junk"],
)
def test_anything_not_positively_local_is_hosted(url: str) -> None:
    assert not endpoint_is_local(url)
    assert hosting_for(url) == "hosted"


def test_classification_never_resolves_a_name(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("endpoint classification must not resolve names")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)

    assert not endpoint_is_local("http://db.internal.corp/v1")


def test_an_origin_drops_everything_that_could_carry_a_secret() -> None:
    origin = endpoint_origin("https://user:pass@llm.example.com:8443/v1/tenant-42?key=abc#frag")

    assert origin == "https://llm.example.com:8443"
    assert endpoint_origin("not a url") == "unknown"


# --- the provider block -----------------------------------------------------


def test_a_custom_endpoint_is_unknown_jurisdiction_with_derived_hosting() -> None:
    hosted = block_for_endpoint("openai_compatible", preset=None, base_url="https://x.example/v1")
    local = block_for_endpoint("openai_compatible", preset=None, base_url="http://localhost:1/v1")

    assert (hosted.hosting, hosted.jurisdiction) == ("hosted", "unknown")
    assert (local.hosting, local.jurisdiction) == ("local", "not_applicable")


def test_a_preset_supplies_its_declared_jurisdiction() -> None:
    block = block_for_endpoint("openai_compatible", preset=get_preset("mistral"), base_url=None)

    assert (block.hosting, block.jurisdiction, block.preset) == ("hosted", "FR", "mistral")


@pytest.mark.parametrize(
    ("name", "hosting", "jurisdiction"),
    [
        ("echo", "local", "not_applicable"),
        ("openai", "hosted", "unknown"),
        ("anthropic", "hosted", "unknown"),
        ("bedrock", "hosted", "unknown"),
        ("something_custom", "unknown", "unknown"),
    ],
)
def test_every_provider_gets_a_complete_block(name: str, hosting: str, jurisdiction: str) -> None:
    block = describe_provider(types.SimpleNamespace(name=name))

    assert isinstance(block, ProviderBlock)
    assert (block.provider, block.hosting, block.jurisdiction) == (name, hosting, jurisdiction)


def test_an_ollama_provider_is_described_from_its_endpoint() -> None:
    local = describe_provider(
        types.SimpleNamespace(name="ollama", _base_url="http://localhost:11434")
    )
    remote = describe_provider(types.SimpleNamespace(name="ollama", _base_url="https://o.example"))

    assert (local.hosting, local.jurisdiction) == ("local", "not_applicable")
    assert (remote.hosting, remote.jurisdiction) == ("hosted", "unknown")


def test_the_policy_request_block_has_the_shape_a_policy_engine_matches() -> None:
    block = block_for_endpoint("openai_compatible", preset=get_preset("groq"), base_url=None)

    assert block.to_policy_request("some-model") == {
        "model": {"provider": "openai_compatible", "name": "some-model", "hosting": "hosted"},
        "provider": {
            "name": "groq",
            "hosting": "hosted",
            "jurisdiction": "US",
            "jurisdiction_verification": "attested",
        },
    }


# --- config and orchestrator wiring ----------------------------------------


def _fake_openai(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    module = types.ModuleType("openai")
    for name in ("AuthenticationError", "RateLimitError", "APIError"):
        setattr(module, name, type(name, (Exception,), {}))
    client = MagicMock()
    client.close = AsyncMock()
    module.AsyncOpenAI = MagicMock(return_value=client)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)
    return module.AsyncOpenAI  # type: ignore[attr-defined,no-any-return]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OLLAMA_BASE_URL",
        "FORGE_OPENAI_COMPATIBLE_BASE_URL",
        "FORGE_OPENAI_COMPATIBLE_PRESET",
        "FORGE_OPENAI_COMPATIBLE_API_KEY_ENV",
        "AWS_ACCESS_KEY_ID",
        "AWS_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)
    # Building default providers probes AWS credentials through boto3, which
    # can try the instance-metadata address. Tests stay offline.
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setattr(Orchestrator, "_aws_credentials_available", lambda self: False)
    return monkeypatch


class _Records(logging.Handler):
    """Collect every record the ``forge`` logger handles."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))

    @property
    def text(self) -> str:
        return " | ".join(self.messages)


@pytest.fixture
def forge_logs(monkeypatch: pytest.MonkeyPatch) -> _Records:
    """Capture the ``forge`` logger.

    It does not propagate to the root, and the orchestrator reconfigures it on
    construction, so the handler is re-attached after every reconfiguration.
    """
    from forge.observability import logging as forge_logging

    records = _Records()
    original = forge_logging.configure_logging

    def configure(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        logger = logging.getLogger("forge")
        logger.addHandler(records)
        logger.setLevel(logging.DEBUG)

    monkeypatch.setattr(forge_logging, "configure_logging", configure)
    monkeypatch.setattr("forge.orchestration.orchestrator.configure_logging", configure)
    return records


def test_config_reads_the_three_fields_from_the_environment(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_BASE_URL", "http://localhost:8000/v1")
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_PRESET", "local_vllm")
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_API_KEY_ENV", "MY_KEY")

    config = ForgeConfig.from_env()

    assert config.openai_compatible_base_url == "http://localhost:8000/v1"
    assert config.openai_compatible_preset == "local_vllm"
    assert config.openai_compatible_api_key_env == "MY_KEY"


def test_the_defaults_configure_no_compatible_endpoint(clean_env: pytest.MonkeyPatch) -> None:
    config = ForgeConfig.from_env()

    assert config.openai_compatible_base_url is None
    assert config.openai_compatible_preset is None
    assert config.openai_compatible_api_key_env is None


def test_with_no_base_url_the_openai_provider_is_built_exactly_as_before(
    clean_env: pytest.MonkeyPatch,
) -> None:
    # The existing behaviour, pinned: one keyword, the key, and nothing else.
    constructor = _fake_openai(clean_env)
    clean_env.setenv("OPENAI_API_KEY", "sk-existing")

    orchestrator = Orchestrator(config=ForgeConfig.from_env())

    constructor.assert_called_once_with(api_key="sk-existing")
    assert "openai" in orchestrator.providers
    assert "openai_compatible" not in orchestrator.providers
    assert orchestrator.providers["openai"].token_param_for("o3") == "max_completion_tokens"
    assert orchestrator.providers["openai"].token_param_for("gpt-4o") == "max_tokens"


def test_a_preset_wires_the_compatible_provider_beside_openai(
    clean_env: pytest.MonkeyPatch,
) -> None:
    constructor = _fake_openai(clean_env)
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_PRESET", "local_lmstudio")

    orchestrator = Orchestrator(config=ForgeConfig.from_env())

    provider = orchestrator.providers["openai_compatible"]
    assert "openai" not in orchestrator.providers
    assert provider.provider_block.preset == "local_lmstudio"
    assert constructor.call_args.kwargs["base_url"] == "http://localhost:1234/v1"


def test_a_hosted_endpoint_without_a_key_is_skipped_without_logging_anything_secret(
    clean_env: pytest.MonkeyPatch, forge_logs: _Records
) -> None:
    _fake_openai(clean_env)
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_BASE_URL", "https://llm.example.com/v1/tenant-7")
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_API_KEY_ENV", "ABSENT_KEY")
    clean_env.setenv("UNRELATED_SECRET", "sk-should-never-appear")

    orchestrator = Orchestrator(config=ForgeConfig.from_env())

    assert "openai_compatible" not in orchestrator.providers
    assert "OpenAI-compatible provider unavailable" in forge_logs.text
    assert "tenant-7" not in forge_logs.text
    assert "sk-should-never-appear" not in forge_logs.text


def test_the_key_never_reaches_logs_when_the_provider_is_wired(
    clean_env: pytest.MonkeyPatch, forge_logs: _Records
) -> None:
    _fake_openai(clean_env)
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_BASE_URL", "https://llm.example.com/v1")
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_API_KEY_ENV", "MY_KEY")
    clean_env.setenv("MY_KEY", "sk-wired-secret-value")

    orchestrator = Orchestrator(config=ForgeConfig.from_env())

    assert "openai_compatible" in orchestrator.providers
    assert "sk-wired-secret-value" not in forge_logs.text
    assert "sk-wired-secret-value" not in repr(orchestrator.providers["openai_compatible"])
    assert "sk-wired-secret-value" not in orchestrator.config.model_dump_json()


async def test_every_model_call_event_carries_the_provider_block(orchestrator) -> None:
    started = []
    orchestrator.events.subscribe(
        lambda event: started.append(event) if event.type is EventType.MODEL_CALL_STARTED else None
    )

    await orchestrator.run("write a short plan")

    assert started
    for event in started:
        assert event.data["provider"] == "echo"
        assert event.data["hosting"] == "local"
        assert event.data["jurisdiction"] == "not_applicable"
        assert "provider_preset" in event.data


async def test_the_run_context_builds_a_policy_request_block(orchestrator) -> None:
    captured = {}

    def capture(event) -> None:
        if event.type is EventType.MODEL_CALL_STARTED and not captured:
            captured.update(event.data)

    orchestrator.events.subscribe(capture)
    await orchestrator.run("write a short plan")
    block = describe_provider(orchestrator.providers["echo"]).to_policy_request(captured["model"])

    assert block["model"] == {"provider": "echo", "name": captured["model"], "hosting": "local"}
    assert block["provider"]["jurisdiction"] == "not_applicable"


# --- documentation review ---------------------------------------------------


def test_a_reviewed_preset_records_the_date_its_documentation_was_read() -> None:
    presets = load_presets()
    reviewed = {pid for pid, preset in presets.items() if preset.review == "reviewed"}

    # "reviewed" means the base_url was read on the vendor's documentation
    # page, never that the vendor's API was contacted.
    assert reviewed == EXPECTED_PRESETS - {"mistral"}
    for preset_id in reviewed:
        assert presets[preset_id].reviewed_on == "2026-11-24"
    assert presets["mistral"].reviewed_on is None
    assert presets["mistral"].notes


@pytest.mark.parametrize(
    "mutation",
    [
        {"review": "reviewed", "reviewed_on": None},
        {"review": "unreviewed", "reviewed_on": "2026-11-24"},
        {"review": "reviewed", "reviewed_on": "yesterday"},
    ],
)
def test_a_review_and_its_date_must_agree(mutation: dict) -> None:
    document = _raw_document()
    document["presets"] = {"local_vllm": {**document["presets"]["local_vllm"], **mutation}}

    with pytest.raises(ConfigurationError, match="reviewed_on"):
        parse_presets(document)


# --- user-supplied preset files --------------------------------------------


def _user_file(tmp_path, presets: dict) -> str:
    path = tmp_path / "my-presets.json"
    path.write_text(
        json.dumps({"schema_version": PRESETS_SCHEMA_VERSION, "presets": presets}),
        encoding="utf-8",
    )
    return str(path)


def _user_preset(**overrides) -> dict:
    preset = {
        "base_url": "https://llm.internal.example.com/v1",
        "docs_url": "https://docs.internal.example.com/llm",
        "jurisdiction": "DE",
        "hosting": "hosted",
        "api_key_env": "INTERNAL_LLM_KEY",
        "token_param": "max_tokens",
        "compatibility": "documented",
        "review": "unreviewed",
        "reviewed_on": None,
        "notes": "",
    }
    preset.update(overrides)
    return preset


def test_a_user_file_is_loaded_alongside_the_built_in_presets(tmp_path) -> None:
    path = _user_file(tmp_path, {"company_gateway": _user_preset()})

    presets = load_presets(path)

    assert set(presets) > EXPECTED_PRESETS
    assert presets["company_gateway"].source == "user"
    assert presets["mistral"].source == "built_in"
    assert set(load_presets()) == EXPECTED_PRESETS


def test_a_user_presets_jurisdiction_is_reported_as_attested(tmp_path) -> None:
    path = _user_file(tmp_path, {"company_gateway": _user_preset()})
    preset = get_preset("company_gateway", path)

    block = block_for_endpoint("openai_compatible", preset=preset, base_url=None)

    assert (block.jurisdiction, block.jurisdiction_verification) == ("DE", "attested")
    assert block.preset_source == "user"
    assert block.to_event()["jurisdiction_verification"] == "attested"
    assert block.to_event()["provider_preset_source"] == "user"
    assert block.to_policy_request("m")["provider"]["jurisdiction_verification"] == "attested"


def test_a_built_in_jurisdiction_is_attested_too() -> None:
    # Shipping a value here does not make it verified: it is still somebody's
    # statement about a vendor.
    block = block_for_endpoint("openai_compatible", preset=get_preset("groq"), base_url=None)

    assert block.jurisdiction_verification == "attested"
    assert block.preset_source == "built_in"


def test_unknown_and_local_jurisdictions_are_not_called_attested() -> None:
    unknown = block_for_endpoint("openai_compatible", preset=None, base_url="https://x.example/v1")
    local = block_for_endpoint("openai_compatible", preset=None, base_url="http://localhost:1/v1")

    assert unknown.jurisdiction_verification == "unknown"
    assert local.jurisdiction_verification == "not_applicable"


def test_a_user_preset_may_not_reuse_a_built_in_id(tmp_path) -> None:
    # Never a silent override: that would change what a well-known name points
    # at, and its jurisdiction, while logs and policies kept showing the name.
    path = _user_file(tmp_path, {"mistral": _user_preset(), "company_gateway": _user_preset()})

    with pytest.raises(ConfigurationError, match="may not reuse the id") as raised:
        load_presets(path)

    assert raised.value.context["colliding_presets"] == ["mistral"]


def test_repointing_a_user_preset_resets_its_jurisdiction(tmp_path) -> None:
    path = _user_file(tmp_path, {"company_gateway": _user_preset()})
    preset = get_preset("company_gateway", path)

    block = block_for_endpoint(
        "openai_compatible", preset=preset, base_url="https://elsewhere.example.com/v1"
    )

    assert (block.jurisdiction, block.preset, block.preset_source) == ("unknown", None, None)


@pytest.mark.parametrize(
    "content",
    ["", "not json", "[]", '{"schema_version": "provider-presets.v1"}'],
)
def test_a_malformed_user_file_is_refused(tmp_path, content: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationError):
        load_presets(str(path))


def test_a_missing_user_file_is_refused_without_echoing_its_path(tmp_path) -> None:
    missing = tmp_path / "secret-project-name" / "presets.json"

    with pytest.raises(ConfigurationError) as raised:
        load_presets(str(missing))

    assert "secret-project-name" not in str(raised.value)


def test_a_user_preset_is_validated_like_a_built_in_one(tmp_path) -> None:
    path = _user_file(tmp_path, {"company_gateway": _user_preset(hosting="local")})

    with pytest.raises(ConfigurationError, match="base_url contradicts"):
        load_presets(path)


def test_the_orchestrator_wires_a_user_preset_from_config(
    clean_env: pytest.MonkeyPatch, tmp_path
) -> None:
    constructor = _fake_openai(clean_env)
    path = _user_file(tmp_path, {"company_gateway": _user_preset()})
    clean_env.setenv("FORGE_PROVIDER_PRESETS_PATH", path)
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_PRESET", "company_gateway")
    clean_env.setenv("INTERNAL_LLM_KEY", "sk-internal-fake")

    config = ForgeConfig.from_env()
    orchestrator = Orchestrator(config=config)

    assert config.provider_presets_path == path
    block = orchestrator.providers["openai_compatible"].provider_block
    assert (block.preset, block.preset_source, block.jurisdiction) == (
        "company_gateway",
        "user",
        "DE",
    )
    assert constructor.call_args.kwargs["base_url"] == "https://llm.internal.example.com/v1"


def test_a_colliding_user_file_leaves_the_provider_unwired_and_says_why(
    clean_env: pytest.MonkeyPatch, tmp_path, forge_logs: _Records
) -> None:
    _fake_openai(clean_env)
    path = _user_file(tmp_path, {"groq": _user_preset()})
    clean_env.setenv("FORGE_PROVIDER_PRESETS_PATH", path)
    clean_env.setenv("FORGE_OPENAI_COMPATIBLE_PRESET", "groq")

    orchestrator = Orchestrator(config=ForgeConfig.from_env())

    assert "openai_compatible" not in orchestrator.providers
    assert "may not reuse the id" in forge_logs.text
