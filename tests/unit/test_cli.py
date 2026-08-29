"""Smoke tests for the CLI surface."""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from docmax import __version__
from docmax.cli.main import app
from docmax.core.branding import APP_NAME

runner = CliRunner()

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """Strip Rich's ANSI styling, e.g. around the version number under FORCE_COLOR."""
    return _ANSI_ESCAPE.sub("", text)


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert APP_NAME in plain(result.stdout)
    assert __version__ in plain(result.stdout)


def test_help_lists_doctor() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in plain(result.stdout)


def test_bare_invocation_orients_the_user() -> None:
    """A bare invocation must never leave the user staring at nothing.

    Since M7 it launches the TUI — but only where there is a person at a
    terminal to use one. Under a test runner there is not, so it prints help,
    which is the same behaviour a pipe, a CI step and a cron job get. Exit 0:
    help is not a failure.
    """
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Usage" in plain(result.stdout)


def test_a_bare_invocation_launches_the_tui_at_a_real_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the rule above, with the guards satisfied."""
    import docmax.cli.main as main_module
    from docmax.cli import interactive

    launched: list[bool] = []
    monkeypatch.setattr(interactive, "is_interactive", lambda: True)
    monkeypatch.setattr(main_module, "_launch_tui", lambda: launched.append(True))
    monkeypatch.setattr(main_module, "_tui_is_available", lambda: True)

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert launched == [True]


def test_a_bare_invocation_never_launches_the_tui_under_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Textual app writes escape sequences to the stream ADR 0017 reserves
    for one JSON object."""
    import docmax.cli.main as main_module
    from docmax.cli import interactive

    launched: list[bool] = []
    monkeypatch.setattr(interactive, "is_interactive", lambda: True)
    monkeypatch.setattr(main_module, "_launch_tui", lambda: launched.append(True))
    monkeypatch.setattr(main_module, "_tui_is_available", lambda: True)

    result = runner.invoke(app, ["--json"])

    assert result.exit_code == 0
    assert launched == []
    assert "Usage" in plain(result.stdout)


def test_the_tui_command_refuses_a_non_interactive_terminal() -> None:
    """Never a hang, never a screenful of escapes into a pipe."""
    result = runner.invoke(app, ["tui"])
    assert result.exit_code == 1
    assert "Traceback" not in plain(result.stdout)


def test_the_tui_command_refuses_json() -> None:
    result = runner.invoke(app, ["--json", "tui"])
    assert result.exit_code == 1


def test_doctor_reports_without_mutating_anything() -> None:
    """`doctor` is read-only. `setup` (M3) is the one that installs."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "tesseract" in plain(result.stdout)
