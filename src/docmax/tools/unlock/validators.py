"""Checks run against the staged output of ``unlock``, before it replaces anything.

Validating the staged file rather than the destination is what makes "the
operation produced a broken file" a condition the user never observes: the check
runs while the destination is still untouched, and a failure discards the staged
file instead of delivering it.

This is ``protect``'s validator inverted, and it matters for the same reason.
The failure worth catching is a file that still needs a password -- because the
next thing that happens to an unlocked document is usually an automated step
that has no password to give it, and finding out there is still one is a job
that fails somewhere else entirely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmax.core.errors import OutputValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from docmax.core.protocols import Validator


def decrypts_to(expected: int) -> Validator:
    """Build a validator asserting the staged file opens with no password and is intact.

    A factory because the expected page count is only known at call time: it
    depends on the document, which the strategy read on the way through.
    """

    def validate(produced: Path) -> None:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        try:
            reader = PdfReader(str(produced))
            encrypted = bool(reader.is_encrypted)
        except (PyPdfError, OSError, ValueError) as exc:
            raise OutputValidationError(
                f"The output of 'unlock' could not be reopened as a PDF: {exc}",
                context={"path": str(produced)},
            ) from exc

        if encrypted:
            raise OutputValidationError(
                "The output of 'unlock' still needs a password.",
                context={"path": str(produced)},
            )

        try:
            actual = len(reader.pages)
        except (PyPdfError, OSError, ValueError) as exc:
            raise OutputValidationError(
                f"The pages of the output of 'unlock' could not be read: {exc}",
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


__all__ = ["decrypts_to"]
