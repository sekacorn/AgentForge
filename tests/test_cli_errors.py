"""CLI error messages print as plain text: square brackets are never read as Rich markup."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

import forge.memory
from forge.cli import main as cli_main
from forge.cli.main import app
from forge.exceptions import ConfigurationError, ForgeError

# Every shape that Rich markup would change: a style tag, a closing tag with
# nothing to close (Rich raises MarkupError on it), an extra in brackets, and a
# link-like tag.
MESSAGE = "install 'agentforge-oss[sqlite]' then fix [bold]this[/] and [/] [link=https://x.example]y[/link]"


def _all_output(result: Any) -> str:
    stderr = ""
    try:
        stderr = result.stderr
    except ValueError:  # older click: stderr not captured separately
        pass
    return result.output + stderr


def _plain(text: str) -> str:
    """Rich wraps long lines; compare without the line breaks it inserted."""
    return " ".join(text.split())


def test_fail_prints_the_message_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_config: Any) -> Any:
        raise ConfigurationError(MESSAGE)

    monkeypatch.setattr(forge.memory, "build_memory", broken)
    result = CliRunner().invoke(app, ["memory", "migrate"])
    assert result.exit_code == 2
    assert f"Error: {MESSAGE}" in _plain(_all_output(result))


def test_run_errors_print_the_message_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: Any, *_args: Any, **_kwargs: Any) -> Any:
        raise ForgeError(MESSAGE)

    monkeypatch.setattr(cli_main.Orchestrator, "run_sync", broken)
    result = CliRunner().invoke(app, ["run", "anything"])
    assert result.exit_code == 1
    assert f"Error: {MESSAGE}" in _plain(_all_output(result))


def test_error_text_never_parses_markup() -> None:
    text = cli_main._error_text(ValueError(MESSAGE))
    assert text.plain == f"Error: {MESSAGE}"
    # Only the label is styled; the message carries no spans of its own.
    assert [(span.start, span.end) for span in text.spans] == [(0, len("Error:"))]
