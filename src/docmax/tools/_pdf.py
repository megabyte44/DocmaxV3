"""Opening and saving PDFs, the same way in every tool.

Eight tools open a PDF and map the same three failures onto the same three typed
errors. Written once here, they cannot drift into eight slightly different
messages for the same problem — which is the difference between a user learning
one behaviour and learning eight.

Private to ``tools``, like ``_pagespec``: no package of its own, so the
registry's directory walk never sees it.

**pypdf is imported inside the functions**, not at module scope. A tool package's
``tool.py`` is read on every ``--help``; if importing a tool's helpers pulled in
pypdf, discovery would cost what running costs. ``tests/hygiene/test_no_heavy_imports.py``
checks this in a subprocess.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.core.errors import (
    CorruptDocumentError,
    EncryptedDocumentError,
    UnsupportedFormatError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pypdf import PdfReader, PdfWriter

    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import Validator

#: The only extension these tools read. A tool that grows a second one overrides
#: this rather than widening it for everybody.
PDF_SUFFIX = ".pdf"


def require_pdf(document: DocumentRef) -> None:
    """Refuse anything that is not a PDF, by extension.

    Separate from :func:`open_pdf` because ``get-info`` has to probe an
    encrypted file before it can open it properly, and a Word document should
    still be reported as the wrong format rather than as a damaged PDF.
    """
    if document.suffix != PDF_SUFFIX:
        raise UnsupportedFormatError(
            f"This tool only reads PDFs, and {document.path.name} is not one.",
            context={"path": str(document.path), "suffix": document.suffix},
        )


def open_pdf(document: DocumentRef) -> PdfReader:
    """Open one input, or raise the typed error that names what is wrong.

    Three distinct failures get three distinct errors, because "it failed" sends
    a user looking in the wrong place: a Word document, a damaged file, and a
    password-protected one each need a different next step.
    """
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    require_pdf(document)

    try:
        reader = PdfReader(str(document.path))
    except (PyPdfError, OSError, ValueError) as exc:
        raise CorruptDocumentError(
            f"{document.path.name} could not be read as a PDF: {exc}",
            context={"path": str(document.path)},
        ) from exc

    if reader.is_encrypted:
        # Checked before touching .pages, which raises its own error for this
        # case with a far less useful message.
        raise EncryptedDocumentError(
            f"{document.path.name} is password-protected.",
            context={"path": str(document.path)},
        )

    return reader


def page_count(reader: PdfReader) -> int:
    """How many pages, translating a late parse failure into a typed error.

    ``PdfReader`` is lazy: a file can open and only fail when the page tree is
    walked. Without this, that failure would escape a tool untyped and be
    reported as an internal error rather than a damaged document.
    """
    from pypdf.errors import PyPdfError

    try:
        return len(reader.pages)
    except (PyPdfError, OSError, ValueError) as exc:
        raise CorruptDocumentError(
            f"The page tree could not be read: {exc}",
        ) from exc


def save(
    writer: PdfWriter,
    target: OutputTarget,
    *,
    validators: Sequence[Validator] = (),
) -> None:
    """Write ``writer`` to ``target`` through the atomic mechanism.

    Every tool goes through here rather than calling ``atomic_write`` itself,
    so that "the validators run before the swap" is a property of the helper
    rather than of each author remembering. ``core/atomic.py`` remains the only
    module that touches a destination.
    """
    from docmax.core.atomic import atomic_write

    with atomic_write(target, validators=validators) as handle:
        writer.write(handle)


def metadata_of(reader: PdfReader) -> dict[str, str]:
    """Document metadata as plain strings, with pypdf's objects left behind.

    pypdf returns its own text objects; a ``ToolResult`` travels into logs and
    into ``--json``, so what leaves a tool has to be ordinary data.
    """
    raw: Any = reader.metadata
    if not raw:
        return {}
    return {str(key): str(value) for key, value in raw.items() if value is not None}


__all__ = ["PDF_SUFFIX", "metadata_of", "open_pdf", "page_count", "require_pdf", "save"]
