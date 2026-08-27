"""The local engine for ``from-images``.

## Order is argument order, and page size is image size

Neither is a parameter, and both decisions are worth defending because the
obvious alternative is to add a flag.

**Order.** The shell already sorts: ``docmax from-images scans/*.png`` arrives
in the order the shell globbed, and a user who wants a different order says so
by listing the files. A ``--sort`` flag would be a second ordering that disagrees
with the one the user can see on their command line.

**Page size.** Each page is exactly as large as its image at that image's own
resolution, so nothing is scaled, cropped or letterboxed. A ``--page-size a4``
would mean deciding what to do with an image of the wrong aspect ratio, and
every answer to that loses something the user did not ask to lose.

## Why img2pdf and not Pillow alone

Pillow can write a PDF directly, in one call. It also *re-encodes* every image
to do it, so a folder of JPEG scans comes out larger and visibly worse. img2pdf
embeds a JPEG's existing bytes untouched, which is the whole reason
``dependencies.md`` names it in the ``images`` extra.

Pillow is still here, doing the job it is good at: proving a file is the image
its extension claims before img2pdf is asked to embed it. A ``.png`` that is
actually a text file is then one clear error naming that file, rather than an
img2pdf failure part-way through a batch.

## Assembly goes through pypdf

Each image becomes a one-page PDF and the pages are collected into a single
``PdfWriter``. Doing it per image rather than handing img2pdf the whole list is
what makes progress advance per image and cancellation take effect between
images rather than only at the end -- and it means the output is saved by
``_pdf.save``, the same helper every other tool uses.

Every heavy import sits inside a method. The module costs nothing to import.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.branding import DIST_NAME
from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _formats
from docmax.tools._pdf import save
from docmax.tools.from_images.validators import page_count_is

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

#: Installed by the ``images`` extra. ``PIL`` is the import name of Pillow.
DEPENDENCIES = ("img2pdf", "PIL")

#: Built from ``DIST_NAME`` because ``core/branding.py`` is the only module
#: allowed to spell the brand out; ``tests/hygiene/test_branding.py`` enforces it.
INSTALL_HINT = f'Install them with: pip install "{DIST_NAME}[images]"'


def missing_dependencies() -> tuple[str, ...]:
    """Everything this engine needs and cannot find.

    Reported by name rather than as a single "unavailable", because a user with
    Pillow but not img2pdf needs to be told which of the two to install.
    """
    return tuple(name for name in DEPENDENCIES if importlib.util.find_spec(name) is None)


class FromImagesLocal:
    """Assemble images into a PDF with img2pdf, page by page."""

    def is_available(self) -> bool:
        return not missing_dependencies()

    def unavailable_reason(self) -> str | None:
        missing = missing_dependencies()
        if not missing:
            return None
        return f"Not installed: {', '.join(missing)}. {INSTALL_HINT}"

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Combine every document in ``docs`` into ``target``, one page per image.

        ``OutputTarget.resolve`` has already refused a destination that is one of
        these inputs, which is the check that matters most here: the obvious
        mistake is ``from-images *.png -o page1.png``, and without it the first
        image would be destroyed by the document built from it.
        """
        import io
        import time

        import img2pdf
        from pypdf import PdfReader, PdfWriter

        if not docs:
            raise InvalidParameterError(
                "From-images needs at least one image.",
                remedy="Pass the images to combine, in the order they should appear.",
            )

        # Every input is checked before any work starts, so a bad file at
        # position forty is reported before thirty-nine pages have been built.
        for document in docs:
            _require_image(document)

        started = time.monotonic()
        writer = PdfWriter()
        formats: list[str] = []

        progress.start(f"Adding {len(docs)} image(s)", total=len(docs))
        for document in docs:
            cancellation.raise_if_cancelled(operation="from-images")

            known = _formats.image_for_suffix(document.suffix)
            formats.append(known.name if known else document.suffix.lstrip("."))

            try:
                page_bytes = img2pdf.convert(str(document.path))
            except Exception as exc:
                # img2pdf's errors are a set of unrelated Exception subclasses
                # with no common base, so there is nothing narrower to catch.
                # Everything reaching here is about this one file, and saying
                # which file is the useful part.
                raise InvalidParameterError(
                    f"{document.path.name} could not be placed in a PDF: {exc}",
                    remedy="Check the image opens in a viewer.",
                    context={"path": str(document.path)},
                ) from exc

            writer.add_page(PdfReader(io.BytesIO(page_bytes)).pages[0])
            progress.advance()

        save(writer, target, validators=(page_count_is(len(docs)),))

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "pages": len(docs),
                "images": len(docs),
                # In argument order, which is page order. Reported so a caller
                # can see what went in and in what sequence.
                "formats": formats,
            },
        )


def _require_image(document: DocumentRef) -> None:
    """Refuse anything that is not an image DocMax reads, before any page is built.

    Two checks, and both are needed. The extension decides which format the file
    *claims* to be, and Pillow decides whether it is one -- a ``.png`` holding a
    text file passes the first and fails the second, and that is precisely the
    file that would otherwise fail somewhere deep inside img2pdf.
    """
    from PIL import Image, UnidentifiedImageError

    known = _formats.image_for_suffix(document.suffix)
    if known is None or not known.readable:
        raise _reject(document)

    try:
        with Image.open(document.path) as handle:
            # `verify` reads the file's structure without decoding every pixel,
            # which is the right depth here: it catches truncation and the wrong
            # container, and costs nothing on a large photograph.
            handle.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        from docmax.core.errors import CorruptDocumentError

        raise CorruptDocumentError(
            f"{document.path.name} is not a readable {known.label} image: {exc}",
            remedy="Check the file opens in an image viewer.",
            context={"path": str(document.path), "format": known.name},
        ) from exc


def _reject(document: DocumentRef) -> Exception:
    from docmax.core.errors import UnsupportedFormatError

    return UnsupportedFormatError(
        f"{document.path.name} is not an image format `from-images` reads.",
        context={"path": str(document.path), "suffix": document.suffix},
    )


def _version() -> str:
    from importlib.metadata import version

    return f"img2pdf/{version('img2pdf')}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return FromImagesLocal()


__all__ = ["DEPENDENCIES", "FromImagesLocal", "build", "missing_dependencies"]
