"""Checks run against the staged output of ``convert``, before it replaces anything.

Validating the staged file rather than the destination is what makes "the
operation produced a broken file" a condition the user never observes: the check
runs while the destination is still untouched, and a failure discards the staged
file instead of delivering it.

``atomic_path`` already refuses a staged file that is missing or empty, which
covers the commonest way an external program fails while reporting success. What
is left for this module is the format-shaped check: a ``.docx`` that is not a
zip archive is not a Word document, however many bytes it has.

The check is deliberately shallow -- a container check, not a validity check.
Proving a document is *well-formed* would mean parsing every one of eight
formats, which would be re-implementing Pandoc in order to check Pandoc. What is
caught here is the failure that actually happens: a writer that emitted a
diagnostic, a fragment, or an empty container under a name that promises
otherwise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmax.core.errors import OutputValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from docmax.core.protocols import Validator
    from docmax.tools._formats import Format

#: Formats whose files are zip archives. All three are Open Packaging or ODF
#: containers, and every one of them starts with the local file header ``PK``.
_ZIP_CONTAINERS = frozenset({"docx", "odt", "epub"})

#: The local file header of a zip entry. ``PK\\x03\\x04`` is a non-empty archive;
#: ``PK\\x05\\x06`` is the end-of-central-directory record of an *empty* one,
#: which is what a failed writer leaves behind and is not a document.
_ZIP_MAGIC = b"PK\x03\x04"


def writes_readable_output(target: Format) -> Validator:
    """Build a validator asserting the staged file is the format it claims to be.

    A factory because the expected format is only known at call time: it is what
    the user asked for with ``--to``.
    """

    def validate(produced: Path) -> None:
        if target.name in _ZIP_CONTAINERS:
            _is_zip_container(produced, target)
        else:
            _is_readable_text(produced, target)

    return validate


def _is_zip_container(produced: Path, target: Format) -> None:
    """``.docx``, ``.odt`` and ``.epub`` are zip archives or they are nothing."""
    try:
        header = produced.read_bytes()[:4]
    except OSError as exc:  # pragma: no cover - the staged file was just written
        raise OutputValidationError(
            f"The output of 'convert' could not be read back: {exc}",
            context={"path": str(produced)},
        ) from exc

    if not header.startswith(_ZIP_MAGIC):
        raise OutputValidationError(
            f"The output of 'convert' is not a valid {target.label} file — "
            "it is not a zip container.",
            context={"path": str(produced), "format": target.name},
        )


def _is_readable_text(produced: Path, target: Format) -> None:
    """The text formats must decode, and must not be only whitespace.

    Decoding is the real check. Pandoc writes UTF-8 for every text format, so a
    file that will not decode is not output that merely disappointed -- it is
    bytes that ended up somewhere they should not have.

    Whitespace-only output is treated as a failure rather than as an empty
    document, on the same reasoning ``atomic_path`` rejects a zero-byte file: a
    conversion that produced nothing has failed, and the destination is better
    left alone.
    """
    try:
        text = produced.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OutputValidationError(
            f"The output of 'convert' is not readable {target.label}: {exc}",
            context={"path": str(produced), "format": target.name},
        ) from exc

    if not text.strip():
        raise OutputValidationError(
            f"The output of 'convert' is empty — no {target.label} was produced.",
            context={"path": str(produced), "format": target.name},
        )


__all__ = ["writes_readable_output"]
