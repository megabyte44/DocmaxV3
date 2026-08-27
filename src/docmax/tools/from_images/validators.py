"""Checks run against the staged output of ``from-images``, before it replaces anything.

Validating the staged file rather than the destination is what makes "the
operation produced a broken file" a condition the user never observes: the check
runs while the destination is still untouched, and a failure discards the staged
file instead of delivering it.

The count is the check that matters for this tool. Every image becomes exactly
one page, so a document with fewer pages than there were images has silently
dropped one -- and a PDF of scans is the worst possible place to lose a page,
because nothing about the file looks wrong afterwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmax.core.errors import OutputValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from docmax.core.protocols import Validator


def is_readable_pdf(produced: Path) -> None:
    """The output must reopen as a PDF with at least one page."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        pages = len(PdfReader(str(produced)).pages)
    except (PyPdfError, OSError, ValueError) as exc:
        raise OutputValidationError(
            f"The output of 'from-images' could not be reopened as a PDF: {exc}",
            context={"path": str(produced)},
        ) from exc

    if pages == 0:
        raise OutputValidationError(
            "The output of 'from-images' has no pages.",
            context={"path": str(produced)},
        )


def page_count_is(expected: int) -> Validator:
    """Build a validator asserting one page per image reached the output.

    A factory because the expected count is only known at call time: it is how
    many images the user passed.
    """

    def validate(produced: Path) -> None:
        from pypdf import PdfReader

        is_readable_pdf(produced)
        actual = len(PdfReader(str(produced)).pages)
        if actual != expected:
            raise OutputValidationError(
                f"Expected {expected} page(s), one per image, found {actual}.",
                context={
                    "path": str(produced),
                    "expected": expected,
                    "actual": actual,
                },
            )

    return validate


__all__ = ["is_readable_pdf", "page_count_is"]
