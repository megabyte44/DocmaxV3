"""Checks run against the staged output of ``protect``, before it replaces anything.

Validating the staged file rather than the destination is what makes "the
operation produced a broken file" a condition the user never observes: the check
runs while the destination is still untouched, and a failure discards the staged
file instead of delivering it.

This tool's check is stronger than the page count every other tool runs, because
its failure mode is worse. A ``protect`` that produced a perfectly readable
*unencrypted* PDF looks like a success from the outside: the command exits zero,
a file appears, and it opens. The user finds out when someone opens it who
should not have been able to. So the staged file is opened, proven to be
encrypted, unlocked with the owner password, and only then counted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmax.core.errors import OutputValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from docmax.core.protocols import Validator


def encrypts_to(expected: int, *, password: str) -> Validator:
    """Build a validator asserting the staged file is encrypted and intact.

    ``password`` is the owner password just written, which is the one that opens
    the file with full access. A factory because neither the password nor the
    page count is known until the strategy has worked them out.
    """

    def validate(produced: Path) -> None:
        from pypdf import PasswordType, PdfReader
        from pypdf.errors import PyPdfError

        try:
            reader = PdfReader(str(produced))
            encrypted = bool(reader.is_encrypted)
        except (PyPdfError, OSError, ValueError) as exc:
            raise OutputValidationError(
                f"The output of 'protect' could not be reopened as a PDF: {exc}",
                context={"path": str(produced)},
            ) from exc

        if not encrypted:
            raise OutputValidationError(
                "The output of 'protect' is not encrypted.",
                context={"path": str(produced)},
            )

        try:
            opened = reader.decrypt(password)
        except (PyPdfError, OSError, ValueError) as exc:
            raise OutputValidationError(
                f"The output of 'protect' could not be unlocked with the password "
                f"it was just given: {exc}",
                context={"path": str(produced)},
            ) from exc

        if opened == PasswordType.NOT_DECRYPTED:
            # A document nobody can open is not a protected document, and this
            # is the only moment it can still be thrown away rather than
            # delivered.
            raise OutputValidationError(
                "The output of 'protect' does not open with the password it was just given.",
                context={"path": str(produced)},
            )

        try:
            actual = len(reader.pages)
        except (PyPdfError, OSError, ValueError) as exc:
            raise OutputValidationError(
                f"The pages of the output of 'protect' could not be read: {exc}",
                context={"path": str(produced)},
            ) from exc

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


__all__ = ["encrypts_to"]
