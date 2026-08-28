"""Checks run against the OCR output before it is swapped into place.

Both engines share these. That is deliberate: a cloud result is validated by the
same code as a local one, so "the remote server returned something odd" fails
here rather than becoming the user's problem.

The two checks catch the two ways OCR goes wrong, and they are different kinds
of wrong. A lost page is the failure every tool guards against. A *blank text
layer* is specific to this one — a wrong language pack, a page rasterised at an
unusable resolution — and it is invisible in a file that otherwise opens
perfectly and looks exactly right.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmax.core.errors import OutputValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from docmax.core.protocols import Validator

#: How much extractable text makes a page count as carrying a text layer.
#:
#: Not one character. A scanned page frequently carries a handful of stray
#: glyphs from a stamp, a page number burned into the image, or a producer
#: watermark, and treating those as "already searchable" would skip exactly the
#: pages that most need recognising. Sixteen is comfortably above that noise and
#: far below a real line of prose.
MIN_TEXT_CHARS = 16


def text_of(page: object) -> str:
    """The extractable text of one pypdf page, or ``""`` when it has none.

    pypdf raises a variety of things on a page whose content stream it cannot
    walk, and a page that cannot be read is a page with no text for our
    purposes — never a reason to fail a document that is otherwise fine.
    """
    try:
        extracted = page.extract_text()  # type: ignore[attr-defined]
    except Exception:
        return ""
    return (extracted or "").strip()


def has_text(page: object) -> bool:
    """Does this page already carry a usable text layer?"""
    return len(text_of(page)) >= MIN_TEXT_CHARS


def is_readable_pdf(produced: Path) -> None:
    """The output must reopen as a PDF with at least one page."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        pages = len(PdfReader(str(produced)).pages)
    except (PyPdfError, OSError, ValueError) as exc:
        raise OutputValidationError(
            f"The output of 'ocr' could not be reopened as a PDF: {exc}",
            context={"path": str(produced)},
        ) from exc

    if pages == 0:
        raise OutputValidationError(
            "The output of 'ocr' has no pages.",
            context={"path": str(produced)},
        )


def has_text_layer(produced: Path) -> None:
    """The output must actually contain extractable text.

    A blank text layer is the characteristic OCR failure — a wrong language
    pack, a page rasterised at an unusable resolution — and it is invisible in
    a file that otherwise looks fine. So it is checked while the destination is
    still untouched, and the remedy names the two parameters that cause it.
    """
    from pypdf import PdfReader

    is_readable_pdf(produced)

    for page in PdfReader(str(produced)).pages:
        if text_of(page):
            return

    raise OutputValidationError(
        "OCR produced a document with no extractable text at all.",
        remedy=(
            "Check --lang matches the document's language, and try a higher "
            "--dpi. A page rasterised too low recognises as nothing."
        ),
        context={"path": str(produced)},
    )


def page_count_is(expected: int) -> Validator:
    """OCR adds a text layer; it must never add or drop a page.

    A factory because the expected count is only known at call time — it comes
    from the user's own copy of the source, opened before anything was sent
    anywhere or written anywhere.
    """

    def validate(produced: Path) -> None:
        from pypdf import PdfReader

        is_readable_pdf(produced)
        actual = len(PdfReader(str(produced)).pages)
        if actual != expected:
            raise OutputValidationError(
                f"OCR changed the page count: expected {expected}, found {actual}.",
                context={"path": str(produced), "expected": expected, "actual": actual},
            )

    return validate


def checks_for(expected_pages: int) -> tuple[Validator, ...]:
    """Every check an OCR result must pass, local or cloud.

    One function so the two engines cannot drift into checking different things
    — which is the whole reason a cloud result is trustworthy at all.
    """
    return (page_count_is(expected_pages), has_text_layer)


__all__ = [
    "MIN_TEXT_CHARS",
    "checks_for",
    "has_text",
    "has_text_layer",
    "is_readable_pdf",
    "page_count_is",
    "text_of",
]
