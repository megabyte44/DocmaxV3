"""Checks run against the merged file *before* it replaces anything.

The point of validating the temp file rather than the destination is that a
wrong result is never delivered at all: the check runs while the destination is
still untouched, and a failure discards the temp file. "Did merge produce a PDF
whose page count equals the sum of its inputs?" is a question worth answering
before the user's disk changes, not after.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmax.core.errors import OutputValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from docmax.core.protocols import Validator


def is_readable_pdf(produced: Path) -> None:
    """The output must open, and must have at least one page."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        pages = len(PdfReader(str(produced)).pages)
    except (PyPdfError, OSError, ValueError) as exc:
        raise OutputValidationError(
            f"The merged file could not be reopened as a PDF: {exc}",
            context={"path": str(produced)},
        ) from exc

    if pages == 0:
        raise OutputValidationError(
            "The merged file has no pages.",
            context={"path": str(produced)},
        )


def page_count_is(expected: int) -> Validator:
    """Build a validator asserting the merged page count.

    A factory rather than a plain function because the expected count is only
    known at call time — it is the sum over the inputs, which the strategy
    counted on the way in.
    """

    def validate(produced: Path) -> None:
        from pypdf import PdfReader

        actual = len(PdfReader(str(produced)).pages)
        if actual != expected:
            raise OutputValidationError(
                f"Expected {expected} pages in the merged file, found {actual}.",
                context={"path": str(produced), "expected": expected, "actual": actual},
            )

    return validate


__all__ = ["is_readable_pdf", "page_count_is"]
