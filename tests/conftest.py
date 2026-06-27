"""Shared pytest fixtures.

Every fixture is fully offline: orchestrators are built with only the
deterministic :class:`EchoProvider`, and the audit log is redirected into the
test's temporary directory. No API key, no network.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from forge import EchoProvider, ForgeConfig, Orchestrator


@pytest.fixture
def make_orchestrator(tmp_path) -> Callable[..., Orchestrator]:
    """Factory that builds an offline orchestrator with a tmp audit log."""

    def _make(config: ForgeConfig | None = None) -> Orchestrator:
        cfg = config or ForgeConfig()
        cfg.compliance.audit_path = str(tmp_path / "audit.jsonl")
        return Orchestrator(cfg, providers={"echo": EchoProvider()})

    return _make


@pytest.fixture
def orchestrator(make_orchestrator) -> Orchestrator:
    """A ready-to-use offline orchestrator."""
    return make_orchestrator()
