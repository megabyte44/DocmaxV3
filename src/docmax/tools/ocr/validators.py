"""Checks run against the OCR output before it is swapped into place.

Both engines share these. That is deliberate: a cloud result is validated by the
same code as a local one, so "the remote server returned something odd" fails
here rather than becoming the user's problem.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmax.core.errors import OutputValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from docmax.core.protocols import Validator


def has_text_layer(produced: Path) -> None:
    """The output must actually contain extractable text.

    A blank text layer is the characteristic OCR failure — a wrong language
    pack, a page rasterised at an unusable resolution — and it is invisible in
    a file that otherwise looks fine.
    """
    raise NotImplementedError


def page_count_is(expected: int) -> Validator:
    """OCR adds a text layer; it must never add or drop a page."""

    def validate(produced: Path) -> None:
        raise NotImplementedError

    return validate


__all__ = ["has_text_layer", "page_count_is"]
