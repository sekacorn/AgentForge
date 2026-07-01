"""Tests that provider credentials are wrapped in SecretStr and never leak."""

from __future__ import annotations

from pydantic import SecretStr

from forge.config import ForgeConfig


def test_api_key_str_is_masked() -> None:
    config = ForgeConfig(api_keys={"anthropic": SecretStr("sk-ant-secret-key")})
    assert "sk-ant-secret-key" not in str(config.api_keys["anthropic"])
    assert "**********" in str(config.api_keys["anthropic"])


def test_api_key_repr_is_masked() -> None:
    config = ForgeConfig(api_keys={"anthropic": SecretStr("sk-ant-secret-key")})
    assert "sk-ant-secret-key" not in repr(config.api_keys["anthropic"])


def test_api_key_get_secret_value_returns_real_value() -> None:
    config = ForgeConfig(api_keys={"anthropic": SecretStr("sk-ant-secret-key")})
    assert config.api_keys["anthropic"].get_secret_value() == "sk-ant-secret-key"


def test_api_key_for_unwraps_secret() -> None:
    config = ForgeConfig(api_keys={"openai": SecretStr("sk-openai-key")})
    assert config.api_key_for("openai") == "sk-openai-key"
    assert config.api_key_for("missing") is None


def test_ollama_base_url_is_secret_str() -> None:
    config = ForgeConfig()
    assert isinstance(config.ollama_base_url, SecretStr)
    assert "localhost" not in str(config.ollama_base_url)
    assert config.ollama_base_url.get_secret_value() == "http://localhost:11434"


def test_config_model_dump_masks_secrets() -> None:
    config = ForgeConfig(api_keys={"anthropic": SecretStr("sk-ant-secret-key")})
    dumped = config.model_dump()
    assert "sk-ant-secret-key" not in str(dumped)
