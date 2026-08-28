"""Opening and saving PDFs, the same way in every tool.

Every tool here opens a PDF and maps the same failures onto the same typed
errors. Written once, they cannot drift into a dozen slightly different messages
for the same problem — which is the difference between a user learning one
behaviour and learning a dozen.

Two entry points, because there are two situations. :func:`open_pdf` refuses an
encrypted document, which is right for every tool that wants to *do* something
to a file. :func:`open_encrypted_pdf` takes a password, for the two tools whose
subject is the lock itself.

Private to ``tools``, like ``_pagespec``: no package of its own, so the
registry's directory walk never sees it.

**pypdf is imported inside the functions**, not at module scope. A tool package's
``tool.py`` is read on every ``--help``; if importing a tool's helpers pulled in
pypdf, discovery would cost what running costs. ``tests/hygiene/test_no_heavy_imports.py``
checks this in a subprocess.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.core.branding import DIST_NAME
from docmax.core.errors import (
    CorruptDocumentError,
    EncryptedDocumentError,
    LocalDependencyMissingError,
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

#: The package pypdf needs in order to read or write anything but RC4, the extra
#: that carries it, and the one sentence that says how to get it.
#:
#: Here rather than in ``protect`` because both halves of the encryption story
#: hit it -- ``open_encrypted_pdf`` below when *reading* an AES file, and
#: ``protect`` when writing one -- and a user must not meet two different
#: install lines for one missing package. Built from ``DIST_NAME`` because
#: ``core/branding.py`` is the only module allowed to spell the brand out;
#: ``tests/hygiene/test_branding.py`` enforces it.
CRYPTO_DEPENDENCY = "cryptography"
CRYPTO_EXTRA = "crypto"
CRYPTO_INSTALL_HINT = f'Install it with: pip install "{DIST_NAME}[{CRYPTO_EXTRA}]"'


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


def open_encrypted_pdf(
    document: DocumentRef,
    password: str | None = None,
) -> tuple[PdfReader, str | None]:
    """Open an input that may be encrypted, using ``password`` when it is.

    The counterpart to :func:`open_pdf`, which refuses an encrypted file
    outright — correctly, because for `rotate` or `merge` a locked document is
    something the user must unlock first. ``unlock`` and ``permissions`` are the
    two tools for which a locked document is the *subject*, so they need a way
    in, and they must both find the same behaviour behind the same errors.

    Returns the reader and how it opened: ``None`` when the file was not
    encrypted at all, otherwise ``"user"`` or ``"owner"`` naming which password
    matched. That distinction is not cosmetic — a PDF's permissions only
    restrict the holder of the *user* password, so a caller reporting them needs
    to know which one it holds.

    A wrong password and a missing one are both
    :class:`EncryptedDocumentError`, with different messages: they need
    different next steps, and neither is a corrupt file.
    """
    from pypdf import PasswordType, PdfReader
    from pypdf.errors import DependencyError, PyPdfError

    require_pdf(document)

    try:
        reader = PdfReader(str(document.path))
        encrypted = bool(reader.is_encrypted)
    except (PyPdfError, OSError, ValueError) as exc:
        raise CorruptDocumentError(
            f"{document.path.name} could not be read as a PDF: {exc}",
            context={"path": str(document.path)},
        ) from exc

    if not encrypted:
        return reader, None

    if password is None:
        raise EncryptedDocumentError(
            f"{document.path.name} is password-protected.",
            remedy="Supply the password with --password.",
            context={"path": str(document.path)},
        )

    try:
        outcome = reader.decrypt(password)
    except DependencyError as exc:
        # AES-encrypted, and `cryptography` is not installed. A missing package
        # is not a bad password, and telling the user to check their password
        # would send them somewhere there is nothing to find.
        raise LocalDependencyMissingError(
            f"{document.path.name} uses an encryption algorithm that needs "
            f"the {CRYPTO_DEPENDENCY!r} package: {exc}",
            dependency=CRYPTO_DEPENDENCY,
            install_hint=CRYPTO_INSTALL_HINT,
            context={"path": str(document.path)},
        ) from exc
    except (PyPdfError, OSError, ValueError) as exc:
        raise CorruptDocumentError(
            f"The encryption in {document.path.name} could not be read: {exc}",
            context={"path": str(document.path)},
        ) from exc

    if outcome == PasswordType.NOT_DECRYPTED:
        raise EncryptedDocumentError(
            f"That password does not open {document.path.name}.",
            remedy="Check the password. Both the user and the owner password work here.",
            context={"path": str(document.path)},
        )

    return reader, "owner" if outcome == PasswordType.OWNER_PASSWORD else "user"


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


def page_geometry(reader: PdfReader, index: int = 0) -> tuple[float, float]:
    """The width and height of one page, in points, translating a late failure.

    Here rather than in ``crop`` because two callers need it and neither should
    learn pypdf's spelling of it: the tool, which validates a box against the
    media it is about to replace, and the box picker, which has to draw a page
    of the right shape before the user can choose anything on it.

    Keeping it here is also what lets a picker satisfy ADR 0005's rule that it
    never imports an engine — it asks this module for a rectangle, exactly as
    the tools do, and learns nothing else about the document.

    Like :func:`page_count`, this walks the page tree, which is where a lazily
    parsed file finally fails.
    """
    from pypdf.errors import PyPdfError

    try:
        box = reader.pages[index].mediabox
        return float(box.width), float(box.height)
    except (PyPdfError, OSError, ValueError, IndexError) as exc:
        raise CorruptDocumentError(
            f"The dimensions of page {index + 1} could not be read: {exc}",
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


__all__ = [
    "CRYPTO_DEPENDENCY",
    "CRYPTO_EXTRA",
    "CRYPTO_INSTALL_HINT",
    "PDF_SUFFIX",
    "metadata_of",
    "open_encrypted_pdf",
    "open_pdf",
    "page_count",
    "page_geometry",
    "require_pdf",
    "save",
]
