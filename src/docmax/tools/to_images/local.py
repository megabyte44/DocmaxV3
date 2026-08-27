"""The local engine for ``to-images``.

## Pillow is not needed, and is not used

``pdftoppm`` draws the page *and writes the file* -- it emits PNG, JPEG and TIFF
itself. Loading the result into Pillow only to write it out again would re-encode
every page for no gain, add a dependency to a tool that does not need one, and
put a second encoder between the user and their pixels. So the ``images`` extra
is ``from-images``' dependency, not this tool's, and this engine's availability
is a single ``shutil.which``.

## One process per page

``pdftoppm`` takes a contiguous ``-f``/``-l`` range, and a page selection like
``1-3,7`` is not contiguous. It also names its own output, padding the page
number to a width that has varied between Poppler releases -- so predicting the
filenames means depending on a detail that is not part of any contract.

Both problems disappear by rendering one page per invocation with
``-singlefile``, which writes exactly ``<root>.<ext>`` and no number at all.
DocMax then names the file, so the naming matches ``split``'s and sorts the way
the document reads.

The cost is real and worth stating: a thousand-page document is a thousand
process launches, which is perhaps twenty seconds of pure overhead. It buys
arbitrary page selections, a progress bar that advances per page, and
cancellation that takes effect within one page rather than at the end.

## The output is a directory

Staged through ``atomic_dir`` and swapped in as a unit, so a run interrupted
after forty of a thousand pages leaves the destination exactly as it was rather
than a directory holding the first forty. ``split`` was the first consumer of
that guarantee; this is the second.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _binaries, _formats, _pagespec
from docmax.tools._pdf import open_pdf, page_count
from docmax.tools.to_images.validators import renders_images

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink
    from docmax.tools._formats import ImageFormat

#: The name in ``_binaries``, declared there since M0 as
#: ``used_by=("ocr", "to-images")``. Poppler, not Pillow.
BINARY = "pdftoppm"

#: Zero-padded so a directory listing sorts the way the document reads. Matches
#: ``split``, because two tools that both number their outputs should not number
#: them differently.
_MIN_DIGITS = 4

#: Resolutions outside this are refused. The floor is where text stops being
#: legible at all; the ceiling is where a single page becomes hundreds of
#: megabytes and the run looks like a hang.
_MIN_DPI = 12
_MAX_DPI = 1200


class ToImagesLocal:
    """Render pages to image files with Poppler's pdftoppm."""

    def is_available(self) -> bool:
        # `shutil.which`, not a subprocess: availability is asked on every
        # routing decision, including the ones that choose a different engine.
        return _binaries.find(BINARY) is not None

    def unavailable_reason(self) -> str | None:
        """Why not, including how to fix it.

        Names Poppler as well as the binary: nobody installs a package called
        ``pdftoppm``, and a user told only the executable's name has to work out
        which project ships it before they can act.
        """
        if self.is_available():
            return None
        return f"{BINARY} (Poppler) is not installed. {_binaries.describe(BINARY).install_hint()}"

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Render the selected pages of ``docs[0]`` into the directory ``target``."""
        import time

        from docmax.core.atomic import atomic_dir

        if not docs:
            raise InvalidParameterError(
                "To-images needs a document.",
                remedy="Pass the PDF to render.",
            )

        image_format = _image_format(params)
        dpi = _dpi(params)

        document = docs[0]

        # The document is read, and the selection resolved, before the binary is
        # looked for -- so a bad `--pages` is reported as a bad selection rather
        # than as a missing dependency.
        reader = open_pdf(document)
        total = page_count(reader)
        selected = _pagespec.parse(params.get("pages"), total=total)

        binary = _binaries.require(BINARY, tool="to-images")
        started = time.monotonic()

        stem = document.path.stem
        width = max(_MIN_DIGITS, len(str(total)))
        written: list[Path] = []

        progress.start(f"Rendering {len(selected)} of {total} page(s)", total=len(selected))

        with atomic_dir(
            target, validators=(renders_images(len(selected), image_format),)
        ) as staged:
            for index in selected:
                # Between pages: nothing is at the destination yet, and the
                # staged directory is discarded wholesale on the way out.
                cancellation.raise_if_cancelled(operation="to-images")

                page = index + 1
                # pdftoppm appends the extension itself, so the root it is given
                # carries none.
                root = staged / f"{stem}-{page:0{width}d}"
                _binaries.run(
                    [
                        binary,
                        str(image_format.rasterise_flag),
                        "-r",
                        str(dpi),
                        # One page, named exactly: `-singlefile` is what stops
                        # pdftoppm appending a page number of its own, whose
                        # width has varied between Poppler releases.
                        "-f",
                        str(page),
                        "-l",
                        str(page),
                        "-singlefile",
                        str(document.path),
                        str(root),
                    ],
                    tool="to-images",
                    cancellation=cancellation,
                )
                written.append(target.destination / f"{root.name}{image_format.suffix}")
                progress.advance()

        return ToolResult(
            outputs=tuple(written),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(binary, cancellation),
            details={
                "files": len(written),
                "pages": len(selected),
                "format": image_format.name,
                "dpi": dpi,
                "selection": _pagespec.describe(selected),
            },
        )


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def _image_format(params: dict[str, Any]) -> ImageFormat:
    """The image format to write, or a typed error listing the real ones."""
    value = params.get("format", "png")
    if value is None:
        value = "png"
    if not isinstance(value, str):
        raise InvalidParameterError(
            f"format must be an image format name, not {value!r}.",
            remedy=f"Use one of: {', '.join(_formats.rasterisable_names())}.",
            context={"parameter": "format"},
        )

    chosen = _formats.image(value)
    if chosen.rasterise_flag is None:
        raise InvalidParameterError(
            f"`to-images` cannot write {chosen.label}.",
            remedy=f"Use one of: {', '.join(_formats.rasterisable_names())}.",
            context={"parameter": "format", "format": chosen.name},
        )
    return chosen


def _dpi(params: dict[str, Any]) -> int:
    """Resolution, bounded at both ends.

    An upper bound because ``--dpi 40000`` is a request that will exhaust memory
    or run for hours, and a user who typed an extra zero is better served by a
    refusal than by a machine that stops responding.
    """
    value = params.get("dpi", 150)
    if value is None:
        return 150
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParameterError(
            f"dpi must be a whole number, not {value!r}.",
            remedy="Try --dpi 150 for screen, or 300 for print.",
            context={"parameter": "dpi"},
        )
    if not _MIN_DPI <= value <= _MAX_DPI:
        raise InvalidParameterError(
            f"dpi must be between {_MIN_DPI} and {_MAX_DPI}, not {value}.",
            remedy="Try --dpi 150 for screen, or 300 for print.",
            context={"parameter": "dpi"},
        )
    return value


def _version(binary: str, cancellation: CancellationToken) -> str:
    """Whatever actually did the work, in the same form the cloud engine reports.

    Best-effort: a version probe that failed must not fail a render that already
    succeeded. ``pdftoppm -v`` writes to stderr rather than stdout, which is
    unusual enough to be worth saying out loud.
    """
    from docmax.core.errors import DocMaxError

    try:
        completed = _binaries.run([binary, "-v"], tool="to-images", cancellation=cancellation)
    except DocMaxError:
        return f"{BINARY}/unknown"

    reported = (completed.stderr or completed.stdout or b"").decode("utf-8", errors="replace")
    first = reported.strip().splitlines()
    return f"{BINARY}/{first[0].strip() if first else 'unknown'}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return ToImagesLocal()


__all__ = ["BINARY", "ToImagesLocal", "build"]
