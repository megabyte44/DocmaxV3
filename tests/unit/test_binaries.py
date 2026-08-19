"""Finding and running external programs.

The subprocess tests use **the Python interpreter as a stand-in binary**. That
is not a mock: it exercises the real `Popen`, the real timeout, and the real
kill path, while needing nothing installed on the machine running the suite.
Ghostscript-specific behaviour is tested where Ghostscript is, in
`test_compress.py`.

What matters here is the guarantees v2 lacked: every call has a timeout, and
cancelling reaches a blocked process rather than waiting politely for it.
"""

from __future__ import annotations

import shutil
import sys
import time

import pytest

from docmax.core.cancellation import NEVER_CANCELLED, CancellationToken
from docmax.core.errors import (
    CancelledError,
    ExternalToolFailedError,
    ExternalToolTimeoutError,
    LocalDependencyMissingError,
)
from docmax.tools import _binaries


def python(code: str) -> list[str]:
    """An argv that runs `code` — a real subprocess, no install required."""
    return [sys.executable, "-c", code]


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------


def test_every_declared_binary_names_the_tools_that_need_it() -> None:
    for binary in _binaries.EXTERNAL_BINARIES:
        assert binary.used_by, f"{binary.name} is declared but nothing uses it"


def test_ghostscript_is_declared_for_compress() -> None:
    assert "compress" in _binaries.describe("gs").used_by


def test_ghostscript_knows_its_windows_spellings() -> None:
    """`gs` is `gswin64c` on Windows; looking only for `gs` reports it missing.

    And it must be the *console* build — bare `gswin64` opens a window and never
    returns, which would hang a batch run rather than failing it.
    """
    candidates = _binaries.describe("gs").candidates()

    assert "gs" in candidates
    assert "gswin64c" in candidates
    assert "gswin64" not in candidates


def test_every_binary_offers_an_install_hint() -> None:
    """A report a user cannot act on is only half a report."""
    for binary in _binaries.EXTERNAL_BINARIES:
        assert binary.install_hint()


def test_an_unknown_binary_is_a_programming_error() -> None:
    with pytest.raises(KeyError):
        _binaries.describe("nonexistent-binary")


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------


def test_find_locates_something_that_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gs" if name == "gs" else None)

    assert _binaries.find("gs") == "/usr/bin/gs"


def test_find_tries_every_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows only the third candidate exists."""
    monkeypatch.setattr(
        shutil, "which", lambda name: r"C:\gs\gswin32c.exe" if name == "gswin32c" else None
    )

    assert _binaries.find("gs") == r"C:\gs\gswin32c.exe"


def test_find_returns_none_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)

    assert _binaries.find("gs") is None


def test_require_names_the_tool_and_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)

    with pytest.raises(LocalDependencyMissingError) as caught:
        _binaries.require("gs", tool="compress")

    assert caught.value.dependency == "gs"
    assert "compress" in str(caught.value)
    assert caught.value.remedy, "the install line is the whole point"


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def test_a_successful_run_returns_its_output() -> None:
    completed = _binaries.run(python("print('hello')"), tool="test", cancellation=NEVER_CANCELLED)

    assert completed.returncode == 0
    assert b"hello" in completed.stdout


def test_a_non_zero_exit_becomes_a_typed_error() -> None:
    with pytest.raises(ExternalToolFailedError) as caught:
        _binaries.run(
            python("import sys; sys.stderr.write('it went wrong'); sys.exit(3)"),
            tool="test",
            cancellation=NEVER_CANCELLED,
        )

    assert caught.value.context["returncode"] == 3
    assert "it went wrong" in str(caught.value), "the program's own complaint reaches the user"


def test_a_missing_executable_becomes_a_typed_error() -> None:
    with pytest.raises(ExternalToolFailedError):
        _binaries.run(
            ["definitely-not-a-real-binary-xyz"], tool="test", cancellation=NEVER_CANCELLED
        )


def test_a_long_complaint_is_trimmed() -> None:
    """Ghostscript prints pages of banner; an error panel nobody reads is no error."""
    with pytest.raises(ExternalToolFailedError) as caught:
        _binaries.run(
            python("import sys; sys.stderr.write('x' * 5000); sys.exit(1)"),
            tool="test",
            cancellation=NEVER_CANCELLED,
        )

    assert len(str(caught.value)) < 1000


def test_a_deadline_kills_a_hung_program() -> None:
    """v2 had no timeout anywhere, so a hung xelatex hung DocMax indefinitely."""
    token = CancellationToken(timeout=0.4)
    started = time.monotonic()

    with pytest.raises(ExternalToolTimeoutError):
        _binaries.run(python("import time; time.sleep(30)"), tool="test", cancellation=token)

    assert time.monotonic() - started < 20, "it was killed, not waited out"


def test_the_default_timeout_applies_without_a_deadline() -> None:
    """There is no way to call `run` without a timeout, which is the point."""
    started = time.monotonic()

    with pytest.raises(ExternalToolTimeoutError):
        _binaries.run(
            python("import time; time.sleep(30)"),
            tool="test",
            cancellation=NEVER_CANCELLED,
            default_timeout=0.4,
        )

    assert time.monotonic() - started < 20


def test_cancelling_kills_a_running_program() -> None:
    """A cooperative token cannot interrupt a blocked wait; the process is killed.

    Without this, Ctrl-C during a two-minute compression would do nothing at all
    until the compression finished on its own.
    """
    import threading

    token = CancellationToken()
    threading.Timer(0.3, token.cancel).start()
    started = time.monotonic()

    with pytest.raises(CancelledError):
        _binaries.run(python("import time; time.sleep(30)"), tool="test", cancellation=token)

    assert time.monotonic() - started < 20


def test_a_cancelled_run_is_not_reported_as_a_failure() -> None:
    """A killed process exits non-zero; that is the user's choice, not a fault."""
    import threading

    token = CancellationToken()
    threading.Timer(0.3, token.cancel).start()

    with pytest.raises(CancelledError):
        _binaries.run(python("import time; time.sleep(30)"), tool="test", cancellation=token)


def test_the_kill_registration_is_released_after_success() -> None:
    """Otherwise a finished operation's token keeps a handle on a dead process."""
    token = CancellationToken()

    _binaries.run(python("pass"), tool="test", cancellation=token)
    token.cancel()  # must not explode on a process that has already exited

    assert token.is_cancelled
