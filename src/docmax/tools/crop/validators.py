"""Checks run against the staged output of ``crop``, before it replaces anything.

Validating the staged file rather than the destination is what makes "the
operation produced a broken file" a condition the user never observes: the check
runs while the destination is still untouched, and a failure discards the staged
file instead of delivering it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmax.core.errors import OutputValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from docmax.core.protocols import Validator
    from docmax.tools._box import PageBox

#: How far a written rectangle may sit from the one that was asked for before
#: it counts as wrong. PDF numbers are serialised as decimal text, so an exact
#: float comparison would fail on rounding that no viewer could show.
_TOLERANCE = 0.5


def is_readable_pdf(produced: Path) -> None:
    """The output must reopen as a PDF with at least one page."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        pages = len(PdfReader(str(produced)).pages)
    except (PyPdfError, OSError, ValueError) as exc:
        raise OutputValidationError(
            f"The output of 'crop' could not be reopened as a PDF: {exc}",
            context={"path": str(produced)},
        ) from exc

    if pages == 0:
        raise OutputValidationError(
            "The output of 'crop' has no pages.",
            context={"path": str(produced)},
        )


def cropped_to(
    box: PageBox,
    *,
    expected_pages: int,
    skipped: frozenset[int] = frozenset(),
) -> Validator:
    """Every page kept, and every page the strategy cropped actually that size.

    Two checks rather than one, because a crop can fail in two directions and
    they need different words. Losing a page is the failure ``reorder`` and
    ``rotate`` guard against. Writing a page that is still its original size is
    the failure specific to this tool: pypdf accepts a rectangle assignment that
    silently does nothing if it is handed the wrong object, and the result opens
    perfectly well — it is simply not cropped.

    ``skipped`` carries the 1-based pages the strategy deliberately left alone
    because the box did not fit on them. They are exempt from the size check and
    from nothing else — a validator that did not know about them would fail
    every mixed-size document, and one that simply checked "some page changed
    size" would pass a run that cropped one page out of forty.
    """

    def validate(produced: Path) -> None:
        from pypdf import PdfReader

        is_readable_pdf(produced)
        reader = PdfReader(str(produced))
        actual_pages = len(reader.pages)
        if actual_pages != expected_pages:
            raise OutputValidationError(
                f"Expected {expected_pages} page(s) in the output, found {actual_pages}.",
                context={
                    "path": str(produced),
                    "expected": expected_pages,
                    "actual": actual_pages,
                },
            )

        for number, page in enumerate(reader.pages, start=1):
            if number in skipped:
                continue
            media = page.mediabox
            width, height = float(media.width), float(media.height)
            if abs(width - box.width) > _TOLERANCE or abs(height - box.height) > _TOLERANCE:
                raise OutputValidationError(
                    f"Page {number} of the output is {width:g}x{height:g} points, "
                    f"not the {box.width:g}x{box.height:g} that was requested.",
                    context={
                        "path": str(produced),
                        "page": number,
                        "expected": [box.width, box.height],
                        "actual": [width, height],
                    },
                )

    return validate


__all__ = ["cropped_to", "is_readable_pdf"]
