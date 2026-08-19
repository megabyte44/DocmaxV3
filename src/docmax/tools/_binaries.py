"""Finding and running the external programs some engines need.

Four tools eventually shell out — Ghostscript, Tesseract, Pandoc, Poppler — and
they all need the same three things: locate the binary, refuse helpfully when it
is absent, and run it with a timeout that cannot be forgotten. Written once here.

**Where this lives, and why.** The mapping used to sit in ``cli/main.py``, where
only ``doctor`` could reach it: ``tools`` is below ``cli`` in the layering and
cannot import upward. Moving it down means ``doctor`` and the engines consult
one list instead of two that can disagree — and the knowledge "compress needs
Ghostscript" belongs beside the tool that needs it rather than beside the
command that reports on it. ``cli`` may import ``tools``, so ``doctor`` still
reads it.

Private to ``tools``, like ``_pdf`` and ``_pagespec``: underscore-prefixed with
no package of its own, so the registry's directory walk never sees it.

**Every call has a timeout, and it is not optional.** v2 had none anywhere, so a
hung ``xelatex`` hung the whole application indefinitely. :func:`run` takes the
deadline from the cancellation token, which is exactly what
``CancellationToken.remaining_seconds`` exists for — the operating system
enforces the limit for the one thing a cooperative token cannot poll.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from docmax.core.errors import (
    ExternalToolFailedError,
    ExternalToolTimeoutError,
    LocalDependencyMissingError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from docmax.core.cancellation import CancellationToken


@dataclass(frozen=True, slots=True)
class Binary:
    """One external program, and how to find and install it."""

    #: The canonical name, as ``doctor`` reports it and the roadmap names it.
    name: str
    #: Tools that cannot run their local engine without it.
    used_by: tuple[str, ...]
    #: Every command name to try, in order. Ghostscript is the reason this is a
    #: list rather than a string: the console executable is ``gswin64c`` on
    #: Windows and ``gs`` everywhere else, so looking only for ``gs`` would
    #: report it missing on a machine where it is installed.
    commands: tuple[str, ...] = ()
    #: What to type, per platform, to get it.
    install: dict[str, str] = field(default_factory=dict)

    def candidates(self) -> tuple[str, ...]:
        return self.commands or (self.name,)

    def install_hint(self) -> str:
        """The install line for this platform, or every line if unrecognised."""
        platform = (
            "windows"
            if sys.platform == "win32"
            else ("macos" if sys.platform == "darwin" else "linux")
        )
        specific = self.install.get(platform)
        if specific:
            return f"Install it with: {specific}"
        if self.install:
            joined = "; ".join(f"{key}: {value}" for key, value in sorted(self.install.items()))
            return f"Install it — {joined}"
        return f"Install {self.name} and make sure it is on your PATH."


#: Every external program DocMax knows about, and which tools want it.
#:
#: Only ``gs`` is reachable today; the rest are declared because ``doctor``
#: reports on them and has since M0, and because a list that grows tool by tool
#: is a list that gets out of step with the roadmap.
EXTERNAL_BINARIES: tuple[Binary, ...] = (
    Binary(
        name="gs",
        used_by=("compress", "pdfa"),
        # gswin64c/gswin32c are the *console* builds. The bare `gswin64` opens a
        # window and never returns, which would hang a batch run.
        commands=("gs", "gswin64c", "gswin32c"),
        install={
            "linux": "apt install ghostscript",
            "macos": "brew install ghostscript",
            "windows": "winget install ArtifexSoftware.GhostScript",
        },
    ),
    Binary(
        name="tesseract",
        used_by=("ocr",),
        install={
            "linux": "apt install tesseract-ocr",
            "macos": "brew install tesseract",
            "windows": "winget install UB-Mannheim.TesseractOCR",
        },
    ),
    Binary(
        name="pdftoppm",
        used_by=("ocr", "to-images"),
        install={
            "linux": "apt install poppler-utils",
            "macos": "brew install poppler",
            "windows": "winget install oschwartz10612.Poppler",
        },
    ),
    Binary(
        name="pandoc",
        used_by=("convert",),
        install={
            "linux": "apt install pandoc",
            "macos": "brew install pandoc",
            "windows": "winget install JohnMacFarlane.Pandoc",
        },
    ),
)

_BY_NAME = {binary.name: binary for binary in EXTERNAL_BINARIES}


def describe(name: str) -> Binary:
    """The declaration for ``name``. Raises ``KeyError`` for an unknown binary."""
    return _BY_NAME[name]


def find(name: str) -> str | None:
    """The absolute path to ``name``, or ``None`` if it is not installed.

    Uses ``shutil.which``, so it is cheap enough to call on every routing
    decision — which is what ``is_available`` does, including on the decisions
    that end up choosing a different engine.
    """
    for command in describe(name).candidates():
        found = shutil.which(command)
        if found:
            return found
    return None


def require(name: str, *, tool: str) -> str:
    """The path to ``name``, or a typed error naming the tool and the fix.

    Carries enough context for the router to offer the cloud engine instead:
    this is the exact moment the Cloud Engine justifies its existence.
    """
    found = find(name)
    if found is not None:
        return found

    binary = describe(name)
    raise LocalDependencyMissingError(
        f"{tool} needs {binary.name}, which is not installed.",
        dependency=binary.name,
        install_hint=binary.install_hint(),
        context={"tool": tool, "binary": binary.name},
    )


def run(
    command: Sequence[str | Path],
    *,
    tool: str,
    cancellation: CancellationToken,
    default_timeout: float = 300.0,
) -> subprocess.CompletedProcess[bytes]:
    """Run an external program, with a deadline and a kill switch.

    Two guarantees this exists to provide:

    **It always has a timeout.** ``cancellation.remaining_seconds()`` when the
    caller set a deadline, ``default_timeout`` otherwise. There is no way to
    call this without one, which is the point — v2 had no timeout anywhere and a
    hung subprocess hung DocMax until it was killed by hand.

    **Ctrl-C reaches it.** A cooperative token cannot interrupt a blocked
    ``wait()``, so the process is registered with ``on_cancel`` and killed
    directly. Without that, cancelling during a two-minute compression would do
    nothing until the compression finished.
    """
    timeout = cancellation.remaining_seconds()
    if timeout is None:
        timeout = default_timeout

    argv = [str(part) for part in command]

    try:
        # S603: argv is built by us from a resolved absolute path and validated
        # parameters; nothing here is a shell string and shell= is never used.
        process = subprocess.Popen(  # noqa: S603
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise ExternalToolFailedError(
            f"{tool} could not start {argv[0]}: {exc}",
            context={"tool": tool, "command": argv[0]},
        ) from exc

    release = cancellation.on_cancel(process.kill)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise ExternalToolTimeoutError(
            f"{tool} gave up on {argv[0]} after {timeout:.0f}s.",
            remedy="Try a smaller document, or raise the timeout.",
            context={"tool": tool, "command": argv[0], "timeout_s": timeout},
        ) from exc
    finally:
        release()

    # A killed process reports a non-zero code, so a cancelled run would
    # otherwise be reported as a tool failure rather than as the user's choice.
    cancellation.raise_if_cancelled(operation=tool)

    if process.returncode != 0:
        raise ExternalToolFailedError(
            f"{argv[0]} failed while running {tool} (exit {process.returncode}): {_tail(stderr)}",
            context={
                "tool": tool,
                "command": argv[0],
                "returncode": process.returncode,
            },
        )

    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _tail(stream: bytes | None, *, limit: int = 400) -> str:
    """The useful end of a program's complaint.

    Ghostscript prints pages of banner before the line that matters, and an
    error panel that scrolls the terminal is one nobody reads.
    """
    if not stream:
        return "no output"
    text = stream.decode("utf-8", errors="replace").strip()
    if not text:
        return "no output"
    return text if len(text) <= limit else "…" + text[-limit:]


__all__ = ["EXTERNAL_BINARIES", "Binary", "describe", "find", "require", "run"]
