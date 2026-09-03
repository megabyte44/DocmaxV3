"""Checks run against the staged output of ``watermark-image``, before it replaces anything.

Validating the staged file rather than the destination is what makes "the
operation produced a broken file" a condition the user never observes: the check
runs while the destination is still untouched, and a failure discards the staged
file instead of delivering it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmax.core.errors import OutputValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from docmax.core.protocols import Validator


def is_readable_image(expected_format: str) -> Validator:
    """Build a validator asserting the produced file is a readable image of the expected format.

    A factory because the expected format is only known at call time: it depends
    on the output format specified by the user.
    """

    def validate(produced: Path) -> None:
        from PIL import Image, UnidentifiedImageError

        try:
            image = Image.open(str(produced))
            image.load()  # Force decode to verify the file is actually valid
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise OutputValidationError(
                f"The output of 'watermark-image' could not be reopened as an image: {exc}",
                context={"path": str(produced)},
            ) from exc

    return validate


__all__ = ["is_readable_image"]
