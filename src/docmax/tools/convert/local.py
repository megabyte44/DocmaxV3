"""The local engine for ``convert``.

## What this converts, and what it does not

Pandoc, and only Pandoc. Eight formats -- Markdown, HTML, Word, OpenDocument,
reStructuredText, LaTeX source, EPUB and plain text -- declared in
``tools/_formats.py`` and nowhere else.

**PDF is neither an input nor an output**, which is the surprising part and the
reason [ADR 0011](../../../../docs/adr/0011-convert-is-pandoc-only.md) exists.
Pandoc has no PDF reader at all, and writing PDF means shelling out to a LaTeX
distribution DocMax does not install. Both refusals name the limitation and
point somewhere useful rather than reporting an unknown format, because
``convert report.pdf --to docx`` is the first thing most people will try.

## The shape of the tool

The same shape ``compress`` established at M3, and for the same reasons:

* Output goes through ``atomic_path``, because Pandoc wants a *filename* rather
  than a file object.
* Progress is one indeterminate step. Pandoc reports nothing usable about its
  own progress, and inventing a percentage would be a lie the user cannot check.
* Every call goes through ``_binaries.run``, so the timeout is not optional and
  Ctrl-C kills the process instead of waiting it out.

Nothing here is imported at module scope beyond ``core`` and the format table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _binaries, _formats
from docmax.tools.convert.validators import writes_readable_output

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink
    from docmax.tools._formats import Format

#: The name in ``_binaries``, which owns finding it and the per-platform install
#: line. Declared there since M0 as ``used_by=("convert",)``.
BINARY = "pandoc"


class ConvertLocal:
    """Convert one document into another format by running Pandoc."""

    def is_available(self) -> bool:
        # `shutil.which`, not a subprocess: availability is asked on every
        # routing decision, including the ones that choose a different engine.
        return _binaries.find(BINARY) is not None

    def unavailable_reason(self) -> str | None:
        """Why not, including how to fix it.

        The router quotes this into ``NoEngineAvailableError``, and its own
        remedy can only be generic -- it does not know what Pandoc is. So the
        install line goes here, where the tool that needs it lives, and the user
        gets a command to type rather than a category of problem.
        """
        if self.is_available():
            return None
        return f"{BINARY} is not installed. {_binaries.describe(BINARY).install_hint()}"

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Convert ``docs[0]`` into ``target`` using Pandoc."""
        import time

        from docmax.core.atomic import atomic_path

        if not docs:
            raise InvalidParameterError(
                "Convert needs a document.",
                remedy="Pass the file to convert.",
            )

        document = docs[0]

        # Both formats are settled before the binary is looked for, so a typo in
        # `--to` is reported as a typo rather than as a missing dependency.
        target_format = _target_format(params)
        source_format = _source_format(document)
        standalone = _standalone(params)

        binary = _binaries.require(BINARY, tool="convert")
        started = time.monotonic()

        # One step, not per-anything: Pandoc reports nothing about its own
        # progress, and an indeterminate spinner is the honest rendering.
        progress.start(
            f"Converting {source_format.label} to {target_format.label} with Pandoc",
            total=None,
        )
        cancellation.raise_if_cancelled(operation="convert")

        command: list[str] = [
            binary,
            # Pandoc's own reader and writer names, taken from the table rather
            # than guessed from the extension. `--from`/`--to` are given
            # explicitly so Pandoc never has to infer from a staged filename,
            # which carries a `.part` suffix it would not recognise.
            "--from",
            str(source_format.reader),
            "--to",
            str(target_format.writer),
        ]
        if standalone:
            # Without this Pandoc emits a fragment for formats that have a
            # header -- an HTML file with no <html> element, which opens but is
            # not a document. Binary writers ignore the flag.
            command.append("--standalone")

        with atomic_path(target, validators=(writes_readable_output(target_format),)) as staged:
            _binaries.run(
                [*command, "--output", str(staged), str(document.path)],
                tool="convert",
                cancellation=cancellation,
            )
            progress.advance()

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(binary, cancellation),
            details={
                "from": source_format.name,
                "to": target_format.name,
                "standalone": standalone,
                "source_bytes": document.size_bytes,
                "output_bytes": target.destination.stat().st_size,
            },
        )


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def _target_format(params: dict[str, Any]) -> Format:
    """The format to write, or a typed error that explains rather than refuses.

    A declared-but-unwritable format -- ``pdf`` -- gets the message naming why,
    which is the whole reason it is in the table at all.
    """
    value = params.get("to")
    if value is None:
        raise InvalidParameterError(
            "Convert needs a target format.",
            remedy=f"Pass one with --to, e.g. --to docx. Options: "
            f"{', '.join(_formats.convertible_names())}.",
            context={"parameter": "to"},
        )
    if not isinstance(value, str):
        raise InvalidParameterError(
            f"to must be a format name, not {value!r}.",
            remedy=f"Use one of: {', '.join(_formats.convertible_names())}.",
            context={"parameter": "to"},
        )

    target = _formats.document(value)
    if not target.writable:
        raise _formats.reject_unwritable_document(target)
    return target


def _source_format(document: DocumentRef) -> Format:
    """The format of the input, decided by extension.

    By extension rather than by content sniffing, deliberately: Pandoc decides
    the same way, and a ``.docx`` that is secretly a zip of something else is
    Pandoc's problem to report rather than ours to guess at.
    """
    known = _formats.document_for_suffix(document.suffix)
    if known is None or not known.readable:
        raise _formats.reject_unreadable_document(document.suffix, name=document.path.name)
    return known


def _standalone(params: dict[str, Any]) -> bool:
    value = params.get("standalone", True)
    if isinstance(value, bool):
        return value
    raise InvalidParameterError(
        f"standalone must be true or false, not {value!r}.",
        remedy="Pass --standalone or --no-standalone.",
        context={"parameter": "standalone"},
    )


def _version(binary: str, cancellation: CancellationToken) -> str:
    """Whatever actually did the work, in the same form the cloud engine reports.

    Best-effort: a version probe that failed must not fail a conversion that
    already succeeded, so the result simply says less. Pandoc prints several
    lines and only the first carries the version.
    """
    from docmax.core.errors import DocMaxError

    try:
        completed = _binaries.run([binary, "--version"], tool="convert", cancellation=cancellation)
    except DocMaxError:
        return f"{BINARY}/unknown"

    first = completed.stdout.decode("utf-8", errors="replace").strip().splitlines()
    reported = first[0].removeprefix("pandoc").strip() if first else ""
    return f"{BINARY}/{reported or 'unknown'}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return ConvertLocal()


__all__ = ["BINARY", "ConvertLocal", "build"]
