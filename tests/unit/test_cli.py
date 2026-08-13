"""Smoke tests for the CLI surface."""

from __future__ import annotations

from typer.testing import CliRunner

from docmax import __version__
from docmax.cli.main import app
from docmax.core.branding import APP_NAME

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert APP_NAME in result.stdout
    assert __version__ in result.stdout


def test_help_lists_doctor() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.stdout


def test_bare_invocation_orients_the_user() -> None:
    """A bare invocation must never leave the user staring at nothing.

    Today it prints help. In M7 it will launch the TUI instead — at which point
    this test changes to assert the app starts, and the exit code becomes 0.
    """
    result = runner.invoke(app, [])
    assert "Usage" in result.stdout


def test_doctor_reports_without_mutating_anything() -> None:
    """`doctor` is read-only. `setup` (M3) is the one that installs."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "tesseract" in result.stdout
