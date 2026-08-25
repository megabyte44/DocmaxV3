"""The local engine for ``stamp``.

## The stamp is an input, not a parameter

``docs[0]`` is the document being stamped and ``docs[1]`` is the document
supplying the stamp. That is deliberate, and it is the one design decision in
this tool worth defending.

``OutputTarget.resolve`` refuses a destination that is also an input, and it can
only do that for the inputs it is handed. Had the overlay arrived as a
``--stamp path`` string, ``stamp a.pdf --stamp logo.pdf -o logo.pdf`` would have
consumed the logo while reading it -- exactly the class of bug ADR 0003 exists
to make unrepresentable. Passing it as an input means the existing guarantee
covers it and no new check had to be written.

The CLI still spells it ``--stamp``, because "the file to stamp with" is not the
same kind of argument as "the file to stamp" and making both positional would
make their order load-bearing.

## What gets drawn

The **first page** of the stamp document, and only the first. A multi-page stamp
is almost always a mistake -- a signature block that grew a second page -- and
picking one silently would hide it, so the count is reported in the result.

Nothing is rasterised: the stamp's own content stream is transformed and merged,
so vector artwork stays vector and an embedded font stays embedded. For plain
text over a page, ``watermark`` needs no second document at all.

pypdf is imported inside the methods, not at module scope.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _pagespec, _position
from docmax.tools._pdf import open_pdf, page_count, save
from docmax.tools.stamp.validators import page_count_is

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"


class StampLocal:
    """Merge one document's first page onto the selected pages of another."""

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
        """Stamp ``docs[1]``'s first page onto the selected pages of ``docs[0]``.

        Every page is copied to the output whether or not it was selected, so a
        ``--pages`` selection stamps some pages rather than discarding the rest.
        """
        import time

        from pypdf import PdfWriter, Transformation

        if len(docs) < 2:
            raise InvalidParameterError(
                "Stamp needs two documents: the one to stamp, and the one to stamp with.",
                remedy="Pass the overlay with --stamp logo.pdf.",
            )

        position = _position.canonical(params.get("position") or "bottom-right")
        scale = _scale(params)

        started = time.monotonic()

        stamp_reader = open_pdf(docs[1])
        stamp_pages = page_count(stamp_reader)
        if stamp_pages == 0:
            raise InvalidParameterError(
                f"{docs[1].path.name} has no pages to stamp with.",
                remedy="Use a PDF with at least one page.",
                context={"path": str(docs[1].path)},
            )
        stamp_page = stamp_reader.pages[0]
        stamp_width = float(stamp_page.mediabox.width) * scale
        stamp_height = float(stamp_page.mediabox.height) * scale

        reader = open_pdf(docs[0])
        total = page_count(reader)
        selected = set(_pagespec.parse(params.get("pages"), total=total))

        writer = PdfWriter()
        progress.start(f"Stamping {len(selected)} of {total} page(s)", total=total)
        for index in range(total):
            cancellation.raise_if_cancelled(operation="stamp")
            # Added to the writer *before* it is merged into. pypdf deprecated
            # the other order in 6.x and calls it unreliable: a page that is not
            # yet attached has no writer to register the stamp's resources with,
            # so the merged content stream can reference names never written.
            page = writer.add_page(reader.pages[index])
            if index in selected:
                # Placed per page rather than once: a document may mix page
                # sizes, and a corner computed from page one is the wrong corner
                # for a landscape page later on.
                x, y = _position.place(
                    position,
                    page_width=float(page.mediabox.width),
                    page_height=float(page.mediabox.height),
                    content_width=stamp_width,
                    content_height=stamp_height,
                )
                # Scale first, then translate. The other order would scale the
                # offset too, and the stamp would drift as `--scale` changed.
                page.merge_transformed_page(
                    stamp_page,
                    Transformation().scale(scale).translate(x, y),
                    over=True,
                )
            progress.advance()

        save(writer, target, validators=(page_count_is(total),))

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "pages": total,
                "stamped": len(selected),
                "stamp": docs[1].path.name,
                # Reported so a multi-page stamp document is visible rather than
                # silently reduced to its first page.
                "stamp_pages": stamp_pages,
                "position": position,
                "scale": scale,
            },
        )


def _scale(params: dict[str, Any]) -> float:
    """A positive scale factor, or a typed error naming what is allowed.

    There is no upper bound. A stamp larger than the page is a legitimate
    request -- a full-page "VOID" overlay is exactly that -- and the geometry
    handles it by letting the stamp hang off the edge rather than by refusing.
    """
    value = params.get("scale", 1.0)
    if value is None:
        return 1.0
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidParameterError(
            f"scale must be a number, not {value!r}.",
            remedy="Use 1 for the stamp's own size, or 0.5 for half.",
            context={"parameter": "scale"},
        )
    if value <= 0:
        raise InvalidParameterError(
            f"scale must be greater than zero, not {value}.",
            remedy="Use 1 for the stamp's own size, or 0.5 for half.",
            context={"parameter": "scale"},
        )
    return float(value)


def _version() -> str:
    from importlib.metadata import version

    return f"{DEPENDENCY}/{version(DEPENDENCY)}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return StampLocal()


__all__ = ["StampLocal", "build"]
