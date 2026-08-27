"""Checks run against the staged output of ``to-images``, before it replaces anything.

**This is the validator the mechanism was built for.** ``core/protocols.py``
names the failure in its own docstring: v2 shipped ``extract_images`` output with
``.png`` extensions and no PNG header, producing files nothing could open. The
user got a directory full of plausible-looking filenames and found out later.

So this does not merely count files. It opens each one and checks that it begins
with the byte signature its format requires -- the check that v2's output would
have failed, run while the destination is still untouched.

Header bytes and nothing more, deliberately. Decoding every pixel of every page
would double the cost of the operation to re-verify work Poppler has already
done. What is caught here is the failure that actually happens: a writer that
produced a truncated file, an error message, or nothing at all, under a name
that promises an image.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmax.core.errors import OutputValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from docmax.core.protocols import Validator
    from docmax.tools._formats import ImageFormat

#: Enough bytes for the longest signature in the table, with room to spare. Read
#: per file, so it stays small on purpose.
_HEADER_BYTES = 16


def renders_images(expected: int, image_format: ImageFormat) -> Validator:
    """Build a validator asserting the staged directory holds ``expected`` real images.

    A factory because neither the count nor the format is known until the
    strategy has resolved the user's page selection and ``--format``.
    """

    def validate(produced: Path) -> None:
        rendered = sorted(produced.glob(f"*{image_format.suffix}"))

        if len(rendered) != expected:
            raise OutputValidationError(
                f"Expected {expected} image(s) from 'to-images', found {len(rendered)}.",
                context={
                    "path": str(produced),
                    "expected": expected,
                    "actual": len(rendered),
                    "format": image_format.name,
                },
            )

        for image in rendered:
            _has_a_real_header(image, image_format)

    return validate


def _has_a_real_header(image: Path, image_format: ImageFormat) -> None:
    """One file, proven to begin the way its format must.

    The size check comes first so a zero-byte file is reported as empty rather
    than as having the wrong signature -- they are different failures and the
    first is the one Poppler actually produces when it runs out of disk.
    """
    try:
        header = image.read_bytes()[:_HEADER_BYTES]
    except OSError as exc:  # pragma: no cover - the file was just written
        raise OutputValidationError(
            f"An image written by 'to-images' could not be read back: {exc}",
            context={"path": str(image)},
        ) from exc

    if not header:
        raise OutputValidationError(
            f"'to-images' wrote an empty file: {image.name}",
            context={"path": str(image), "format": image_format.name},
        )

    if not image_format.matches(header):
        raise OutputValidationError(
            f"{image.name} is not a {image_format.label} file — it has no "
            f"{image_format.label} header.",
            remedy="This is a bug in the engine that ran. Please report it.",
            context={"path": str(image), "format": image_format.name},
        )


__all__ = ["renders_images"]
