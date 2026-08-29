"""The local engine for ``crop``.

Cropping a PDF is not a rendering operation. Both the ``/MediaBox`` and the
``/CropBox`` are rectangles in the page dictionary, and trimming a page means
rewriting them — the content stream is untouched and nothing outside the box is
re-encoded. It is also, for the same reason, not destructive at the pixel level:
the marks outside the box are still in the file, merely outside the visible
media. ``sanitize`` is the tool for removing content, and ``crop`` says so
rather than implying a guarantee it does not make.

Both boxes are set, not just one. A viewer shows the ``/CropBox`` where there is
one and the ``/MediaBox`` otherwise, so writing only the media box leaves a
document that looks cropped in some readers and uncropped in others.

pypdf is imported inside the methods, not at module scope: discovery happens on
every ``--help``, and it must not cost what running costs.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _box
from docmax.tools._pdf import open_pdf, page_count, page_geometry, save
from docmax.tools.crop.validators import cropped_to

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"


class CropLocal:
    """Trim every page to one rectangle with pypdf."""

    def is_available(self) -> bool:
        return importlib.util.find_spec(DEPENDENCY) is not None

    def unavailable_reason(self) -> str | None:
        if self.is_available():
            return None
        return f"{DEPENDENCY} is not installed."

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Crop every page of ``docs[0]`` to the requested box, into ``target``."""
        import time

        from pypdf import PdfWriter
        from pypdf.generic import RectangleObject

        if not docs:
            raise InvalidParameterError(
                "Crop needs a document.",
                remedy="Pass the PDF to crop.",
            )

        # Parsed before the document is opened, so a malformed box costs no
        # file I/O and is reported as the input error it is.
        box = _box.parse(params.get("box"))

        started = time.monotonic()
        reader = open_pdf(docs[0])
        total = page_count(reader)

        # Checked against the first page only, and deliberately: a mixed-size
        # document is legal, and refusing the whole run because page 40 is
        # landscape would make the tool unusable on exactly the scanned files
        # that need it most. Pages the box does not fit are reported instead.
        width, height = page_geometry(reader)
        _box.require_within(box, width=width, height=height)

        rectangle = (box.x, box.y, box.right, box.top)
        writer = PdfWriter()
        skipped: list[int] = []

        progress.start(f"Cropping {total} page(s)", total=total)
        for index in range(total):
            cancellation.raise_if_cancelled(operation="crop")
            page = reader.pages[index]
            page_width, page_height = page_geometry(reader, index)
            if box.right > page_width or box.top > page_height:
                # A page the box does not fit on. Left exactly as it was and
                # named in the result, rather than silently produced at the
                # wrong size or used as a reason to fail the other 39 pages.
                skipped.append(index + 1)
            else:
                page.mediabox = RectangleObject(rectangle)
                page.cropbox = RectangleObject(rectangle)
            writer.add_page(page)
            progress.advance()

        if len(skipped) == total:
            raise InvalidParameterError(
                f"The box {box.as_spec()} does not fit on any of the {total} page(s).",
                remedy="Choose a smaller box, or use --interactive to draw one on the page.",
                context={"parameter": "box", "pages": total},
            )

        save(
            writer,
            target,
            validators=(cropped_to(box, expected_pages=total, skipped=frozenset(skipped)),),
        )

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "pages": total,
                "cropped": total - len(skipped),
                "skipped": skipped,
                "box": box.as_spec(),
            },
        )


def _version() -> str:
    from importlib.metadata import version

    return f"{DEPENDENCY}/{version(DEPENDENCY)}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return CropLocal()


__all__ = ["CropLocal", "build"]
