"""The local engine for ``compress``.

The first engine that is not a Python library. Three things follow from that,
and they are the reason this tool is worth reading before writing the next one:

**Output goes through ``atomic_path``, not ``atomic_write``.** Ghostscript needs
a *filename* to write to, so the staged path is handed to it rather than a file
object. The guarantee is the same — stage beside the destination, validate,
rename — and this is its first real consumer.

**Ghostscript can lie about success.** It exits zero having written nothing
under some failures, and it can produce a file that opens but has lost pages.
Both are caught by validators while the destination is still untouched, because
replacing a real document with a smaller broken one is worse than failing.

**Cancellation has to kill a process.** A cooperative token cannot interrupt a
blocked wait, so ``_binaries.run`` registers the process with ``on_cancel``.
Without that, Ctrl-C during a two-minute compression would do nothing until the
compression finished.

Neither pypdf nor Ghostscript is touched at import time: discovery happens on
every ``--help``, and it must not cost what running costs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _binaries
from docmax.tools._pdf import open_pdf, page_count
from docmax.tools.compress.tool import PRESETS
from docmax.tools.compress.validators import page_count_is

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

#: The name in ``_binaries``, which knows the platform's spellings — Ghostscript's
#: console executable is ``gswin64c`` on Windows and ``gs`` everywhere else.
BINARY = "gs"

#: Ghostscript's preset names, passed through as ``-dPDFSETTINGS=/<name>``.
_SETTINGS = {name: f"/{name}" for name in PRESETS}


class CompressLocal:
    """Shrink a PDF by rewriting it through Ghostscript."""

    def is_available(self) -> bool:
        # `shutil.which`, not a subprocess: availability is asked on every
        # routing decision, including the ones that choose a different engine.
        return _binaries.find(BINARY) is not None

    def unavailable_reason(self) -> str | None:
        """Why not, including how to fix it.

        The router quotes this into ``NoEngineAvailableError``, and its own
        remedy can only be generic — it does not know what Ghostscript is. So
        the install line goes here, where the tool that needs it lives, and the
        user gets a command to type rather than a category of problem.
        """
        if self.is_available():
            return None
        return (
            f"{BINARY} (Ghostscript) is not installed. {_binaries.describe(BINARY).install_hint()}"
        )

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Compress ``docs[0]`` into ``target`` using Ghostscript."""
        import time

        from docmax.core.atomic import atomic_path

        if not docs:
            raise InvalidParameterError(
                "Compress needs a document.",
                remedy="Pass the PDF to compress.",
            )

        preset = _preset(params)
        document = docs[0]

        # Validated before the binary is looked for, so a typo in --preset is
        # reported as a typo rather than as a missing dependency.
        reader = open_pdf(document)
        pages = page_count(reader)
        original_bytes = document.size_bytes

        binary = _binaries.require(BINARY, tool="compress")
        started = time.monotonic()

        # One step, not per-page: Ghostscript reports nothing usable about its
        # own progress, and inventing a percentage would be a lie the user
        # cannot check. An indeterminate spinner is the honest rendering.
        progress.start(f"Compressing {pages} page(s) with Ghostscript", total=None)
        cancellation.raise_if_cancelled(operation="compress")

        with atomic_path(target, validators=(page_count_is(pages),)) as staged:
            _binaries.run(
                [
                    binary,
                    "-sDEVICE=pdfwrite",
                    "-dCompatibilityLevel=1.7",
                    f"-dPDFSETTINGS={_SETTINGS[preset]}",
                    # Batch flags: without these Ghostscript waits on stdin for
                    # a prompt nobody is there to answer, which in a script is
                    # indistinguishable from a hang.
                    "-dNOPAUSE",
                    "-dQUIET",
                    "-dBATCH",
                    "-dSAFER",
                    f"-sOutputFile={staged}",
                    str(document.path),
                ],
                tool="compress",
                cancellation=cancellation,
            )
            progress.advance()

        compressed_bytes = target.destination.stat().st_size
        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(binary, cancellation),
            details={
                "pages": pages,
                "preset": preset,
                "original_bytes": original_bytes,
                "compressed_bytes": compressed_bytes,
                # Reported rather than judged. Compression can make a file
                # *larger* — an already-optimised PDF re-encoded at a higher
                # preset does — and saying so is more useful than hiding it.
                "saved_bytes": original_bytes - compressed_bytes,
            },
        )


def _preset(params: dict[str, Any]) -> str:
    """The Ghostscript preset to use, or an error listing the real ones."""
    value = params.get("preset", "ebook")
    if value is None:
        return "ebook"
    if not isinstance(value, str) or value.lstrip("/") not in _SETTINGS:
        raise InvalidParameterError(
            f"{value!r} is not a Ghostscript preset.",
            remedy=f"Use one of: {', '.join(PRESETS)}.",
            context={"parameter": "preset"},
        )
    return value.lstrip("/")


def _version(binary: str, cancellation: CancellationToken) -> str:
    """Whatever actually did the work, in the same form the cloud engine reports.

    Best-effort: a version probe that failed must not fail a compression that
    already succeeded, so the result simply says less.
    """
    from docmax.core.errors import DocMaxError

    try:
        completed = _binaries.run([binary, "--version"], tool="compress", cancellation=cancellation)
    except DocMaxError:
        return "gs/unknown"
    return f"gs/{completed.stdout.decode('utf-8', errors='replace').strip() or 'unknown'}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return CompressLocal()


__all__ = ["BINARY", "CompressLocal", "build"]
