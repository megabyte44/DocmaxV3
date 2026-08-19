"""Checks run against the staged output of ``pages``, before it replaces anything.

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


def is_readable_pdf(produced: Path) -> None:
    """The output must reopen as a PDF with at least one page."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        pages = len(PdfReader(str(produced)).pages)
    except (PyPdfError, OSError, ValueError) as exc:
        raise OutputValidationError(
            f"The output of 'pages' could not be reopened as a PDF: {exc}",
            context={"path": str(produced)},
        ) from exc

    if pages == 0:
        raise OutputValidationError(
            "The output of 'pages' has no pages.",
            context={"path": str(produced)},
        )


def page_count_is(expected: int) -> Validator:
    """Build a validator asserting the page count of the staged file.

    A factory because the expected count is only known at call time: it depends
    on the input and the user's selection, which the strategy worked out on the
    way through.
    """

    def validate(produced: Path) -> None:
        from pypdf import PdfReader

        is_readable_pdf(produced)
        actual = len(PdfReader(str(produced)).pages)
        if actual != expected:
            raise OutputValidationError(
                f"Expected {expected} page(s) in the output, found {actual}.",
                context={
                    "path": str(produced),
                    "expected": expected,
                    "actual": actual,
                },
            )

    return validate


__all__ = ["is_readable_pdf", "page_count_is"]
