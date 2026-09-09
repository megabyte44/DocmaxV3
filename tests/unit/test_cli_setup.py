"""``docmax setup``: installs what ``doctor`` reports missing.

Never lets a test hit a real package manager or a real ``pip install`` — every
test that reaches the "actually run something" path fakes
``_install.install_binary`` / ``_install.install_pip_extra`` instead, the same
way `test_cli_compress.py` fakes `_binaries.find` rather than uninstalling
Ghostscript to test the missing-binary path.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from typer.testing import CliRunner

from docmax.cli.main import app
from docmax.tools import _binaries, _install

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

EXIT_FAILURE = 1


def plain(text: str) -> str:
    return _ANSI.sub("", text)


def shown(result: Any) -> str:
    """Everything a person watching the terminal would see, either stream.

    `setup` splits its own output the way `doctor` and `render_result` already
    do: the plan and the final per-item results are the answer, on stdout;
    confirmation prompts and the installer's own live chatter are commentary,
    on stderr. A human-readable assertion should not need to know which is
    which — matches `test_cli_compress.py`'s own `shown()`.
    """
    return plain(result.stdout) + plain(result.stderr)


def parsed(result: Any) -> dict[str, Any]:
    text = result.stdout
    assert text.strip(), f"nothing on stdout; stderr was: {result.stderr[:400]}"
    body = json.loads(text)
    assert isinstance(body, dict), f"stdout was not a JSON object: {text[:200]}"
    return body


@pytest.fixture(autouse=True)
def isolated_json_state() -> Any:
    """`--json` is module-level state (ADR 0017); reset it either side of a test."""
    from docmax.cli import json_output

    json_output.set_enabled(False)
    yield
    json_output.set_enabled(False)


@pytest.fixture
def gs_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """`compress` needs only `gs`; report it absent regardless of the real machine."""
    monkeypatch.setattr(_binaries, "find", lambda name: None)


@pytest.fixture
def a_manager_is_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A package manager exists, without depending on what this test machine has.

    Pins the platform too: `install_argv_for` looks the platform up itself
    (see its own docstring on why), so a `manager_available()` answer that
    disagrees with the real platform's `install_argv` key would make it
    return `None` regardless of this fixture's intent.
    """
    monkeypatch.setattr(_binaries, "_platform_key", lambda: "linux")
    monkeypatch.setattr(_binaries, "manager_available", lambda: "apt-get")


@pytest.fixture
def no_manager_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_binaries, "manager_available", lambda: None)


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Fail the test the moment anything tries to start a real subprocess."""
    calls: list[object] = []

    def _forbidden(*args: object, **kwargs: object) -> object:
        calls.append(args)
        raise AssertionError(f"a subprocess was started: {args!r}")

    monkeypatch.setattr("subprocess.Popen", _forbidden)
    return calls


# ---------------------------------------------------------------------------
# Nothing missing
# ---------------------------------------------------------------------------


def test_nothing_missing_installs_nothing(no_subprocess: Any) -> None:
    """`merge` is pure pypdf: no binary, no extra, nothing for `setup` to do."""
    result = runner.invoke(app, ["setup", "merge"])

    assert result.exit_code == 0
    assert "Nothing missing" in shown(result)
    assert not no_subprocess


# ---------------------------------------------------------------------------
# --dry-run touches nothing
# ---------------------------------------------------------------------------


def test_dry_run_shows_the_plan_and_starts_no_subprocess(
    gs_missing: None, a_manager_is_on_path: None, no_subprocess: Any
) -> None:
    result = runner.invoke(app, ["setup", "compress", "--dry-run"])

    text = shown(result)
    assert result.exit_code == 0
    assert "gs" in text
    assert "compress" in text
    assert not no_subprocess, "dry-run must never start a real installer"


def test_dry_run_as_json(gs_missing: None, a_manager_is_on_path: None) -> None:
    body = parsed(runner.invoke(app, ["--json", "setup", "compress", "--dry-run"]))

    items = body["result"]["items"]
    assert len(items) == 1
    assert items[0]["name"] == "gs"
    assert items[0]["used_by"] == ["compress"]
    assert items[0]["command"][0] == "apt-get"


def test_dry_run_without_a_package_manager_shows_the_fallback_hint(
    gs_missing: None, no_manager_on_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact copy-pasteable line `doctor` already prints for this binary."""
    monkeypatch.setattr(_binaries, "_platform_key", lambda: "linux")

    result = runner.invoke(app, ["setup", "compress", "--dry-run"])

    assert result.exit_code == 0
    assert "apt-get install -y ghostscript" in shown(result)


# ---------------------------------------------------------------------------
# Refusing to mutate without consent
# ---------------------------------------------------------------------------


def test_refuses_to_install_without_yes_when_not_interactive(
    gs_missing: None, a_manager_is_on_path: None, no_subprocess: Any
) -> None:
    """`CliRunner` is never a tty; `setup` must not hang waiting for a prompt."""
    result = runner.invoke(app, ["setup", "compress"])

    assert result.exit_code == EXIT_FAILURE
    assert not no_subprocess, "refusing must not run anything either"


def test_no_manager_and_nothing_runnable_fails_without_prompting(
    gs_missing: None, no_manager_on_path: None, no_subprocess: Any
) -> None:
    result = runner.invoke(app, ["setup", "compress", "--yes"])

    assert result.exit_code == EXIT_FAILURE
    assert not no_subprocess


# ---------------------------------------------------------------------------
# Actually installing (faked)
# ---------------------------------------------------------------------------


def test_yes_runs_and_reports_a_verified_binary_install(
    gs_missing: None, a_manager_is_on_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_install_binary(binary: Any, manager: str, **kwargs: Any) -> _install.InstallResult:
        calls.append((binary.name, manager))
        return _install.InstallResult(ok=True, verified=True, command=("gs",), stdout_tail="")

    monkeypatch.setattr(_install, "install_binary", fake_install_binary)

    result = runner.invoke(app, ["setup", "compress", "--yes"])

    assert result.exit_code == 0
    assert calls == [("gs", "apt-get")]
    assert "installed" in shown(result)


def test_a_failed_install_exits_non_zero(
    gs_missing: None, a_manager_is_on_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_install_binary(binary: Any, manager: str, **kwargs: Any) -> _install.InstallResult:
        return _install.InstallResult(ok=False, verified=False, command=("gs",), stdout_tail="boom")

    monkeypatch.setattr(_install, "install_binary", fake_install_binary)

    result = runner.invoke(app, ["setup", "compress", "--yes"])

    assert result.exit_code == EXIT_FAILURE
    assert "failed" in shown(result)


def test_yes_as_json_reports_verification_not_just_exit_code(
    gs_missing: None, a_manager_is_on_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A package manager exiting 0 having done nothing must not read as success."""

    def fake_install_binary(binary: Any, manager: str, **kwargs: Any) -> _install.InstallResult:
        return _install.InstallResult(ok=True, verified=False, command=("gs",), stdout_tail="")

    monkeypatch.setattr(_install, "install_binary", fake_install_binary)

    body = parsed(runner.invoke(app, ["--json", "setup", "compress", "--yes"]))

    item = body["result"]["items"][0]
    assert item["installed"] is True
    assert item["verified"] is False


# ---------------------------------------------------------------------------
# Unknown tool
# ---------------------------------------------------------------------------


def test_an_unknown_tool_is_a_typed_error() -> None:
    result = runner.invoke(app, ["setup", "definitely-not-a-real-tool"])

    assert result.exit_code == EXIT_FAILURE
    assert "definitely-not-a-real-tool" in shown(result)
