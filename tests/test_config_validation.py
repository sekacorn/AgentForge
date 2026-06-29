"""Config validation: invalid budgets are rejected at construction time.

Previously a negative cap (or zero workers/steps) was silently accepted and only
surfaced later as a confusing runtime failure; it now fails fast with a clear
ValidationError.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from forge import BudgetConfig


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
