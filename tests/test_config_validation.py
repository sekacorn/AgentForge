"""Config validation: invalid budgets are rejected at construction time.

Previously a negative cap (or zero workers/steps) was silently accepted and only
surfaced later as a confusing runtime failure; it now fails fast with a clear
ValidationError.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from forge import BudgetConfig, ForgeConfig
from forge.config import SecurityConfig
from forge.exceptions import ConfigurationError


def test_negative_usd_budget_rejected() -> None:
    with pytest.raises(ValidationError):
        BudgetConfig(max_usd_per_run=-5.0)


def test_zero_max_workers_rejected() -> None:
    with pytest.raises(ValidationError):
        BudgetConfig(max_workers=0)


def test_negative_max_steps_rejected() -> None:
    with pytest.raises(ValidationError):
        BudgetConfig(max_steps_per_agent=-1)


def test_valid_budget_accepted() -> None:
    cfg = BudgetConfig(max_usd_per_run=0.5, max_tokens_per_run=1000, max_workers=3)
    assert cfg.max_usd_per_run == 0.5
    assert cfg.max_workers == 3


def test_non_positive_tool_timeout_rejected() -> None:
    with pytest.raises(ValidationError):
        SecurityConfig(tool_timeout_seconds=0)


def test_unknown_config_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        ForgeConfig.model_validate({"unexpected": True})


def test_invalid_boolean_environment_value_rejected(monkeypatch) -> None:
    monkeypatch.setenv("FORGE_AUDIT_ENABLED", "sometimes")
    with pytest.raises(ConfigurationError):
        ForgeConfig.from_env()
