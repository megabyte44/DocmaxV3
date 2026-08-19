"""Checks run against the staged output of ``compress``, before it replaces anything.

Ghostscript is the reason this matters more here than elsewhere. It can exit
zero having written nothing, and it can produce a file that is technically a PDF
but has lost pages. Both are caught while the destination is still untouched.
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
            f"Ghostscript produced something that is not a readable PDF: {exc}",
            context={"path": str(produced)},
        ) from exc

    if pages == 0:
        raise OutputValidationError(
            "Ghostscript produced a PDF with no pages.",
            context={"path": str(produced)},
        )


def page_count_is(expected: int) -> Validator:
    """Build a validator asserting no pages were lost.

    Compression must not change what the document *is*. A smaller file with
    fewer pages is not a compressed document, and it is the failure a user would
    be least likely to notice.
    """

    def validate(produced: Path) -> None:
        from pypdf import PdfReader

        is_readable_pdf(produced)
        actual = len(PdfReader(str(produced)).pages)
        if actual != expected:
            raise OutputValidationError(
                f"Compression changed the page count: expected {expected}, found {actual}.",
                context={"path": str(produced), "expected": expected, "actual": actual},
            )

    return validate


__all__ = ["is_readable_pdf", "page_count_is"]
