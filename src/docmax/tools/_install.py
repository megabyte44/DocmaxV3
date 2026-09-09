"""Running installer subprocesses: pip extras and OS package managers.

Distinct from :func:`docmax.tools._binaries.run`: that primitive is for short
tool invocations whose whole output is captured for one error message. An
install can run for minutes — `pip` building `opencv-python-headless` from a
slow mirror — and a user staring at a silent terminal that long will assume it
hung. So this streams output line by line as it arrives instead of buffering
it, and has no short fixed timeout: an install's duration is the package
manager's to decide, not ours to guess at.

This is the one place in `tools` allowed to change something other than a
document. `_binaries.run` only ever calls a program the user already
installed, on a document the user handed it; running an installer is a
different kind of action, and folding it into `_binaries.py` — whose own
docstring is specific to *finding and running* a program a local engine needs
at document-processing time — would blur that.

Never imports `rich` or `typer`. `docmax setup` is the only caller today, but
this stays as interface-agnostic as `_binaries.py` itself: a caller wanting
live output supplies its own `on_output` callback rather than this module
choosing how to print one.

**Verification, not trust.** A package manager can exit `0` having installed
the wrong thing, or already having it, and can exit non-zero after actually
succeeding (a post-install hook failing while the binary it placed on `PATH`
works fine). So `ok` (the process's exit code) and `verified` are reported
separately, and a caller must never treat `ok` alone as success.
`install_binary` sets `verified` itself, via the same `_binaries.find` every
routing decision already uses. `install_pip_extra` cannot: an extra has no
single import name this module could generically check, so it always reports
`verified=False` and leaves the real check to the caller, who re-runs the
affected tool's own `is_available()` through the registry.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from docmax.core.branding import DIST_NAME
from docmax.tools import _binaries

if TYPE_CHECKING:
    from collections.abc import Callable

    from docmax.core.cancellation import CancellationToken
    from docmax.tools._binaries import Binary


@dataclass(frozen=True, slots=True)
class InstallResult:
    """What happened, and whether it actually worked.

    `dry_run` callers get `ok=True, verified=False` and an empty
    `stdout_tail` — nothing ran, so there is nothing to have verified.
    """

    ok: bool
    verified: bool
    command: tuple[str, ...]
    stdout_tail: str


def install_binary(
    binary: Binary,
    manager: str,
    *,
    dry_run: bool,
    cancellation: CancellationToken,
    on_output: Callable[[str], None] | None = None,
) -> InstallResult:
    """Run `binary`'s install command for `manager`, then re-check with `find`.

    `manager` is supplied by the caller — normally `_binaries.manager_available()`
    — rather than detected again here, so a caller that already confirmed a
    manager is on `PATH` cannot silently get a different answer this time.
    """
    argv = binary.install_argv_for(manager)
    if argv is None:
        raise ValueError(f"{binary.name!r} has no install command for manager {manager!r}")

    if dry_run:
        return InstallResult(ok=True, verified=False, command=argv, stdout_tail="")

    streamed = _stream(argv, cancellation=cancellation, on_output=on_output)
    verified = _binaries.find(binary.name) is not None
    return InstallResult(
        ok=streamed.ok,
        verified=verified,
        command=streamed.command,
        stdout_tail=streamed.stdout_tail,
    )


def pip_extra_argv(extra: str) -> tuple[str, ...]:
    """The command :func:`install_pip_extra` runs for `extra`.

    Exposed so a caller — `setup`'s own plan display — can show the exact
    command before running it, without a second, hand-typed copy of the
    same argv drifting from the one that actually runs.
    """
    return (sys.executable, "-m", "pip", "install", f"{DIST_NAME}[{extra}]")


def install_pip_extra(
    extra: str,
    *,
    dry_run: bool,
    cancellation: CancellationToken,
    on_output: Callable[[str], None] | None = None,
) -> InstallResult:
    """`pip install <dist>[extra]`.

    `verified` is always `False` here: unlike a `Binary`, an extra has no
    single import name this module can generically check — it can bring in
    several packages, or the wrong strategy for a tool's own reasons. The
    caller re-checks the affected tool's own `is_available()` through the
    registry, exactly as every routing decision already does, rather than
    this module guessing at what to import.
    """
    argv = pip_extra_argv(extra)

    if dry_run:
        return InstallResult(ok=True, verified=False, command=argv, stdout_tail="")

    streamed = _stream(argv, cancellation=cancellation, on_output=on_output)
    return InstallResult(
        ok=streamed.ok, verified=False, command=streamed.command, stdout_tail=streamed.stdout_tail
    )


def _stream(
    argv: tuple[str, ...],
    *,
    cancellation: CancellationToken,
    on_output: Callable[[str], None] | None,
) -> InstallResult:
    """Run `argv` to completion, forwarding each output line as it arrives.

    `verified` is always `False` here — that check belongs to the caller, who
    knows what "worked" means for this particular install. Ctrl-C reaches a
    blocked install exactly as it reaches a blocked document operation in
    `_binaries.run`: the process is registered with `cancellation.on_cancel`
    and killed directly, since a cooperative token cannot interrupt a blocked
    read on its own.
    """
    try:
        # S603: argv is our own declared install_argv tuple, or
        # [sys.executable, "-m", "pip", ...] — never a shell string, never
        # user-supplied text, and shell= is never used.
        process = subprocess.Popen(  # noqa: S603
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        return InstallResult(ok=False, verified=False, command=argv, stdout_tail=str(exc))

    release = cancellation.on_cancel(process.kill)
    lines: list[str] = []
    try:
        if process.stdout is not None:
            for raw_line in process.stdout:
                line = raw_line.rstrip("\n")
                lines.append(line)
                if on_output is not None:
                    on_output(line)
        process.wait()
    finally:
        release()

    # A killed process reports a non-zero code, so a cancelled install would
    # otherwise be reported as a failed one rather than as the user's choice.
    cancellation.raise_if_cancelled(operation="setup")

    tail = "\n".join(lines[-40:])
    return InstallResult(ok=process.returncode == 0, verified=False, command=argv, stdout_tail=tail)


__all__ = ["InstallResult", "install_binary", "install_pip_extra", "pip_extra_argv"]
