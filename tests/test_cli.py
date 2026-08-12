"""CLI smoke tests that do not require a hosted or local model."""

from __future__ import annotations

from typer.testing import CliRunner

from forge import __version__
from forge.cli.main import app


def test_root_version_option() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output
