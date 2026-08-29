"""What each tool accepts, declared once.

Until M5 every tool accepted exactly one format and ``_pdf.PDF_SUFFIX`` was the
whole of format handling. Three tools now accept sixteen between them, and if
each owned its own list they would drift -- a user who learns ``--to md`` for
one must not find another spells it ``markdown``. So the vocabulary is declared
here, exactly as ``_pagespec`` owns page selections and ``_permissions`` owns
permission names.

[ADR 0010](../../../docs/adr/0010-format-vocabulary.md) records the decision and
what it costs. Two of its rules are worth repeating where the code is:

**The set is closed.** Pandoc supports upwards of forty input formats; DocMax
exposes eight. A format appears here when someone has decided it works and a
test covers it -- not because a dependency happens to accept it.

**A format that cannot be used is still declared.** ``pdf`` is listed with
``convert`` unable to read or write it, and ``txt`` is write-only. Declaring
them is what lets ``--to pdf`` answer with an explanation instead of "unknown
format: pdf". :attr:`Format.unavailable_note` carries the explanation.

``docmax formats`` renders this module and holds no list of its own. Private to
``tools``, like its three predecessors: underscore-prefixed with no package of
its own, so the registry's directory walk never sees it. Nothing here imports a
document library -- the table is data, and reading it must stay as cheap as
``--help``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from docmax.core.branding import APP_NAME
from docmax.core.errors import InvalidParameterError, UnsupportedFormatError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class Format:
    """One format, and what each tool can do with it.

    ``name`` is what a user types (``--to docx``); ``suffixes`` is how a file on
    disk is recognised as this format. They are separate because a format has
    one name and often several spellings on disk -- ``.md``, ``.markdown``,
    ``.mdown`` are all one format and only one of them is a name.
    """

    name: str
    label: str
    #: Every extension that means this format, lower-cased and dotted. The first
    #: is the canonical one.
    suffixes: tuple[str, ...]
    #: Pandoc's own name for the reader, or ``None`` when it has none.
    #: Deliberately Pandoc's spelling rather than ours: someone reading the
    #: Pandoc manual should recognise what DocMax passed it.
    reader: str | None = None
    #: Pandoc's own name for the writer, or ``None`` when it has none.
    writer: str | None = None
    #: Why this format is unusable, when it is. Rendered by ``formats`` and put
    #: into the error, so a refusal explains rather than merely refuses.
    unavailable_note: str | None = None

    @property
    def suffix(self) -> str:
        """The canonical extension, e.g. ``.docx``."""
        return self.suffixes[0]

    @property
    def readable(self) -> bool:
        return self.reader is not None

    @property
    def writable(self) -> bool:
        return self.writer is not None


@dataclass(frozen=True, slots=True)
class ImageFormat:
    """One image format, and which of the image tools handle it.

    Separate from :class:`Format` rather than a flag on it because the two carry
    genuinely different data: a document format knows Pandoc's reader and writer
    names, and an image format knows a pdftoppm flag and a file signature.
    Merging them would give every row several columns that never apply.
    """

    name: str
    label: str
    suffixes: tuple[str, ...]
    #: The pdftoppm flag that writes it, or ``None`` if ``to-images`` cannot.
    rasterise_flag: str | None = None
    #: Whether ``from-images`` reads it.
    readable: bool = True
    #: Leading bytes every file of this format starts with. Any one matching is
    #: enough -- TIFF has two, one per byte order. Empty means unchecked.
    #:
    #: This is the whole point of the type. v2's ``extract_images`` wrote
    #: decoded streams under ``.png`` and ``.tiff`` names with no container
    #: header, producing files nothing could open, and ``core/protocols.py``
    #: cites exactly that as the reason validators exist.
    signatures: tuple[bytes, ...] = field(default_factory=tuple)

    @property
    def suffix(self) -> str:
        return self.suffixes[0]

    def matches(self, header: bytes) -> bool:
        """Does ``header`` begin with one of this format's signatures?"""
        if not self.signatures:
            return True
        return any(header.startswith(signature) for signature in self.signatures)


# ---------------------------------------------------------------------------
# The declarations
# ---------------------------------------------------------------------------

#: Everything ``convert`` knows about. Eight are usable; ``pdf`` is declared and
#: is not -- see :data:`_NO_PDF` and ADR 0011.
_NO_PDF = (
    "Pandoc cannot read PDF, and writing it needs a LaTeX distribution "
    f"{APP_NAME} does not install. Use `to-images` to rasterise a PDF; "
    "PDF conversion arrives with the cloud engine."
)

DOCUMENT_FORMATS: tuple[Format, ...] = (
    Format(
        name="md",
        label="Markdown",
        suffixes=(".md", ".markdown", ".mdown"),
        reader="markdown",
        writer="markdown",
    ),
    Format(
        name="html",
        label="HTML",
        suffixes=(".html", ".htm"),
        reader="html",
        writer="html",
    ),
    Format(
        name="docx",
        label="Word",
        suffixes=(".docx",),
        reader="docx",
        writer="docx",
    ),
    Format(
        name="odt",
        label="OpenDocument Text",
        suffixes=(".odt",),
        reader="odt",
        writer="odt",
    ),
    Format(
        name="rst",
        label="reStructuredText",
        suffixes=(".rst",),
        reader="rst",
        writer="rst",
    ),
    Format(
        name="latex",
        label="LaTeX source",
        suffixes=(".tex", ".latex"),
        reader="latex",
        writer="latex",
        # Writes a .tex file. It does not typeset one, which is why this format
        # needs no LaTeX distribution and `pdf` below does.
    ),
    Format(
        name="epub",
        label="EPUB",
        suffixes=(".epub",),
        reader="epub",
        writer="epub",
    ),
    Format(
        name="txt",
        label="Plain text",
        suffixes=(".txt",),
        # Pandoc's `plain` is a writer with no matching reader, so this format
        # is genuinely one-directional rather than an omission.
        writer="plain",
        unavailable_note="Pandoc has no plain-text reader, so txt is output only.",
    ),
    Format(
        name="pdf",
        label="PDF",
        suffixes=(".pdf",),
        unavailable_note=_NO_PDF,
    ),
)

#: Everything the two image tools know about. ``to-images`` writes the three
#: pdftoppm produces; ``from-images`` reads all five, via Pillow.
IMAGE_FORMATS: tuple[ImageFormat, ...] = (
    ImageFormat(
        name="png",
        label="PNG",
        suffixes=(".png",),
        rasterise_flag="-png",
        signatures=(b"\x89PNG\r\n\x1a\n",),
    ),
    ImageFormat(
        name="jpeg",
        label="JPEG",
        suffixes=(".jpg", ".jpeg"),
        rasterise_flag="-jpeg",
        signatures=(b"\xff\xd8\xff",),
    ),
    ImageFormat(
        name="tiff",
        label="TIFF",
        suffixes=(".tif", ".tiff"),
        rasterise_flag="-tiff",
        # Two byte orders, two magic numbers. A TIFF written on a big-endian
        # machine starts `MM`; everything common today writes `II`.
        signatures=(b"II*\x00", b"MM\x00*"),
    ),
    ImageFormat(
        name="bmp",
        label="Bitmap",
        suffixes=(".bmp",),
        signatures=(b"BM",),
    ),
    ImageFormat(
        name="gif",
        label="GIF",
        suffixes=(".gif",),
        signatures=(b"GIF87a", b"GIF89a"),
    ),
)

_DOCUMENTS: Mapping[str, Format] = {item.name: item for item in DOCUMENT_FORMATS}
_IMAGES: Mapping[str, ImageFormat] = {item.name: item for item in IMAGE_FORMATS}

#: Which declaration each tool draws on. ``formats`` renders this, so a tool
#: that is not listed here is one ``formats`` cannot describe -- which is the
#: correct outcome for the pypdf tools, whose answer is always "PDF".
TOOLS = ("convert", "to-images", "from-images")


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def document(name: str) -> Format:
    """The declaration for a document format name, or a typed error listing the real ones."""
    found = _DOCUMENTS.get(_normalise(name, DOCUMENT_FORMATS))
    if found is None:
        raise InvalidParameterError(
            f"{name!r} is not a format {APP_NAME} converts.",
            remedy=_remedy(convertible_names()),
            context={"parameter": "to", "format": name},
        )
    return found


def image(name: str) -> ImageFormat:
    """The declaration for an image format name, or a typed error listing the real ones."""
    found = _IMAGES.get(_normalise(name, IMAGE_FORMATS))
    if found is None:
        raise InvalidParameterError(
            f"{name!r} is not an image format {APP_NAME} writes.",
            remedy=_remedy(rasterisable_names()),
            context={"parameter": "format", "format": name},
        )
    return found


def convertible_names() -> tuple[str, ...]:
    """Document formats ``convert`` can *write*, in declaration order."""
    return tuple(item.name for item in DOCUMENT_FORMATS if item.writable)


def rasterisable_names() -> tuple[str, ...]:
    """Image formats ``to-images`` can write."""
    return tuple(item.name for item in IMAGE_FORMATS if item.rasterise_flag)


def readable_image_names() -> tuple[str, ...]:
    """Image formats ``from-images`` reads."""
    return tuple(item.name for item in IMAGE_FORMATS if item.readable)


def document_for_suffix(suffix: str) -> Format | None:
    """The document format a file extension means, or ``None`` if none does."""
    wanted = suffix.lower()
    for item in DOCUMENT_FORMATS:
        if wanted in item.suffixes:
            return item
    return None


def image_for_suffix(suffix: str) -> ImageFormat | None:
    """The image format a file extension means, or ``None`` if none does."""
    wanted = suffix.lower()
    for item in IMAGE_FORMATS:
        if wanted in item.suffixes:
            return item
    return None


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def reject_unreadable_document(suffix: str, *, name: str) -> UnsupportedFormatError:
    """The error for an input ``convert`` cannot read.

    A declared-but-unreadable format gets its own note -- so a ``.pdf`` input is
    told *why* Pandoc will not read it and what to use instead, rather than
    being lumped in with a file extension nobody recognises.
    """
    known = document_for_suffix(suffix)
    if known is not None and known.unavailable_note:
        return UnsupportedFormatError(
            f"{name} is {known.label}, which `convert` cannot read. {known.unavailable_note}",
            context={"path": name, "format": known.name},
        )
    if known is not None and not known.readable:
        return UnsupportedFormatError(
            f"{name} is {known.label}, which `convert` cannot read.",
            context={"path": name, "format": known.name},
        )
    return UnsupportedFormatError(
        f"{name} is not a format `convert` reads.",
        context={"path": name, "suffix": suffix},
    )


def reject_unwritable_document(target: Format) -> InvalidParameterError:
    """The error for a ``--to`` naming a declared format that cannot be written."""
    note = target.unavailable_note or f"`convert` cannot write {target.label}."
    return InvalidParameterError(
        f"`convert` cannot write {target.label}. {note}",
        remedy=_remedy(convertible_names()),
        context={"parameter": "to", "format": target.name},
    )


def _remedy(names: Iterable[str]) -> str:
    from docmax.core.branding import CLI_NAME

    return f"Use one of: {', '.join(names)} — or run `{CLI_NAME} formats` for the full table."


def _normalise(name: str, formats: Sequence[Format] | Sequence[ImageFormat]) -> str:
    """Fold what a user might type onto a declared name in ``formats``.

    Accepts the canonical name, any of its suffixes with or without the dot, and
    any case -- so ``DOCX``, ``.docx`` and ``docx`` are one request rather than
    three chances to get it wrong.

    The table to search is a parameter rather than both tables at once, which is
    what keeps this typed: ``Format`` and ``ImageFormat`` are deliberately
    unrelated, so a tuple of both has element type ``object``. It is also the
    more honest lookup -- ``document(".png")`` has no business matching an image
    format on its way to reporting that it is not a document format.

    An unrecognised name is returned unchanged, for the caller's ``dict.get`` to
    miss and turn into an error naming the real options.
    """
    text = name.strip().lower().lstrip(".")
    if not text:
        return text
    dotted = f".{text}"
    for item in formats:
        if text == item.name or dotted in item.suffixes:
            return item.name
    return text


__all__ = [
    "DOCUMENT_FORMATS",
    "IMAGE_FORMATS",
    "TOOLS",
    "Format",
    "ImageFormat",
    "convertible_names",
    "document",
    "document_for_suffix",
    "image",
    "image_for_suffix",
    "rasterisable_names",
    "readable_image_names",
    "reject_unreadable_document",
    "reject_unwritable_document",
]
