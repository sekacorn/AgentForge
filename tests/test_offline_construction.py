"""Constructing an orchestrator must not touch the network.

Building the default providers used to ask boto3 to resolve AWS credentials,
which ends at the instance-metadata address. These tests record every attempt
to open a connection or resolve a name and fail if there is one. The attempts
are recorded rather than raised, because the code under test catches broad
exceptions and a raised guard would be swallowed.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from forge import ForgeConfig, Orchestrator

AWS_VARIABLES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_ROLE_ARN",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_CONFIG_FILE",
    "AWS_EC2_METADATA_DISABLED",
    "FORGE_BEDROCK_ENABLED",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OLLAMA_BASE_URL",
)


@pytest.fixture
def network_attempts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every attempt to connect or resolve, and refuse it."""
    attempts: list[str] = []

    def record(kind: str):
        def guard(*args: object, **kwargs: object):
            attempts.append(f"{kind}{args[1:2] if kind == 'connect' else args[:1]}")
            raise OSError("network access is not allowed in this test")

        return guard

    monkeypatch.setattr(socket.socket, "connect", record("connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", record("connect"))
    monkeypatch.setattr(socket, "create_connection", record("create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", record("getaddrinfo"))
    return attempts


@pytest.fixture
def bare_machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No AWS configuration anywhere: no variables, and an empty home directory."""
    for name in AWS_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _config(tmp_path: Path) -> ForgeConfig:
    config = ForgeConfig.from_env()
    config.compliance.audit_path = str(tmp_path / "audit.jsonl")
    return config


def test_building_default_providers_attempts_no_network_access(
    bare_machine: Path, network_attempts: list[str]
) -> None:
    pytest.importorskip("boto3", reason="the probe only runs when boto3 is installed")

    orchestrator = Orchestrator(_config(bare_machine))

    assert network_attempts == []
    assert "bedrock" not in orchestrator.providers
    assert "echo" in orchestrator.providers


def test_environment_credentials_offer_bedrock_without_a_network_call(
    bare_machine: Path, network_attempts: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("boto3")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAFAKEFAKEFAKEFAKE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-secret-for-an-offline-test")

    orchestrator = Orchestrator(_config(bare_machine))

    assert network_attempts == []
    assert "bedrock" in orchestrator.providers


def test_a_shared_credentials_file_is_a_local_signal(
    bare_machine: Path, network_attempts: list[str]
) -> None:
    pytest.importorskip("boto3")
    (bare_machine / ".aws").mkdir()
    (bare_machine / ".aws" / "credentials").write_text(
        "[default]\naws_access_key_id = AKIAFAKEFAKEFAKEFAKE\naws_secret_access_key = fake\n",
        encoding="utf-8",
    )

    orchestrator = Orchestrator(_config(bare_machine))

    assert network_attempts == []
    assert "bedrock" in orchestrator.providers


def test_an_instance_profile_host_opts_in_explicitly(
    bare_machine: Path, network_attempts: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The one case local signals cannot see. The decision itself is a
    # statement, not a probe, so making it touches no network. What boto3 does
    # once the provider is built is the instance-profile lookup the operator
    # asked for by opting in, and is not exercised here.
    pytest.importorskip("boto3")
    probe = Orchestrator(_config(bare_machine))
    assert probe._aws_credentials_available() is False

    monkeypatch.setenv("FORGE_BEDROCK_ENABLED", "1")
    config = _config(bare_machine)
    probe.config = config

    assert config.bedrock_enabled is True
    assert probe._aws_credentials_available() is True
    assert network_attempts == []


def test_only_half_a_key_pair_is_not_a_signal(
    bare_machine: Path, network_attempts: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("boto3")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAFAKEFAKEFAKEFAKE")

    orchestrator = Orchestrator(_config(bare_machine))

    assert network_attempts == []
    assert "bedrock" not in orchestrator.providers


def test_without_boto3_nothing_is_offered(
    bare_machine: Path, network_attempts: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "boto3" else real(name, *a, **k),
    )
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAFAKEFAKEFAKEFAKE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake")

    orchestrator = Orchestrator(_config(bare_machine))

    assert network_attempts == []
    assert "bedrock" not in orchestrator.providers
