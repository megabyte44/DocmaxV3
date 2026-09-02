"""Checks run against the staged output of ``remove-bg``, before it replaces anything.

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


def is_readable_png(produced: Path) -> None:
    """The output must be a readable PNG with an alpha channel."""
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(produced)
        # Force load of image data to ensure it is readable.
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise OutputValidationError(
            f"The output of 'remove-bg' could not be reopened as a PNG: {exc}",
            context={"path": str(produced)},
        ) from exc

    if image.mode != "RGBA":
        raise OutputValidationError(
            "The output of 'remove-bg' does not have an alpha channel. Expected RGBA mode.",
            context={"path": str(produced), "mode": image.mode},
        )

    if image.size[0] <= 0 or image.size[1] <= 0:
        raise OutputValidationError(
            "The output of 'remove-bg' has zero dimensions.",
            context={"path": str(produced), "size": image.size},
        )


__all__ = ["is_readable_png"]
