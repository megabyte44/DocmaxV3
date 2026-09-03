"""The local engine for ``resize``.

Resizing an image to new dimensions with various fit modes. This is a pure-Python
operation using Pillow; no external binary is needed, and no cloud engine makes
sense — a millisecond-long operation per ADR 0034.

Supports four fit modes: stretch (ignore aspect ratio), cover (crop to fit),
contain (add padding), and fill (alias for cover).
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.branding import DIST_NAME
from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _formats
from docmax.tools.resize.tool import FIT_MODES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

#: Installed by the ``images`` extra. ``PIL`` is the import name of Pillow.
DEPENDENCY = "PIL"

#: Valid fit modes, read from the spec that also renders them as a dropdown.
#: One list, so the values offered and the values accepted cannot drift.
VALID_FIT_MODES = set(FIT_MODES)


def is_available() -> bool:
    """Whether Pillow is importable."""
    return importlib.util.find_spec(DEPENDENCY) is not None


def unavailable_reason() -> str | None:
    """Why not, including how to fix it.

    The router quotes this into ``NoEngineAvailableError``, and its own
    remedy can only be generic — it does not know what Pillow is. So
    the install line goes here, where the tool that needs it lives, and the
    user gets a command to type rather than a category of problem.
    """
    if is_available():
        return None
    return f'Pillow is not installed. Install it with: pip install "{DIST_NAME}[images]"'


class ResizeLocal:
    """Resize an image to new dimensions with Pillow."""

    def is_available(self) -> bool:
        return is_available()

    def unavailable_reason(self) -> str | None:
        return unavailable_reason()

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Resize ``docs[0]`` to the specified dimensions into ``target``."""
        import time

        from PIL import Image

        from docmax.core.atomic import atomic_write
        from docmax.tools.resize.validators import is_readable_image

        if not docs:
            raise InvalidParameterError(
                "Resize needs a document.",
                remedy="Pass the image to resize.",
            )

        # Look up the input format
        input_format = _formats.image_for_suffix(docs[0].suffix)
        if input_format is None or input_format.rasterise_flag is None:
            raise InvalidParameterError(
                f"{docs[0].path.name} is not a format resize handles.",
                remedy=f"Supported formats: {', '.join(_formats.rasterisable_names())}.",
                context={"format": docs[0].suffix},
            )

        # Look up the destination format
        output_format = _formats.image_for_suffix(target.destination.suffix)
        if output_format is None:
            raise InvalidParameterError(
                f"{target.destination.name} is not a recognized image format.",
                remedy=f"Supported formats: {', '.join(_formats.rasterisable_names())}.",
                context={"format": target.destination.suffix},
            )

        document = docs[0]
        started = time.monotonic()

        # Opened before the size is worked out, because `scale` and a lone
        # width or height are all expressed relative to the image's own
        # dimensions -- there is no target size to validate until it is known.
        image = Image.open(str(document.path))

        # Validate parameters
        width, height, sizing = _target_size(params, image.width, image.height)
        fit = _fit(params)
        quality = _quality(params)

        progress.start(f"Resizing {image.format or 'image'}", total=None)
        cancellation.raise_if_cancelled(operation="resize")

        # Perform the resize based on fit mode
        if fit == "stretch":
            resized = image.resize((width, height), Image.Resampling.LANCZOS)
        elif fit == "contain":
            resized = _resize_contain(image, width, height)
        elif fit == "cover" or fit == "fill":
            resized = _resize_cover(image, width, height)
        else:
            # Should not reach here due to _fit validation
            raise InvalidParameterError(
                f"Unknown fit mode: {fit}",
                remedy=f"Use one of: {', '.join(VALID_FIT_MODES)}",
                context={"parameter": "fit"},
            )

        # Handle format-specific saving
        with atomic_write(target, validators=(is_readable_image(output_format.name),)) as handle:
            if output_format.name == "jpeg":
                # JPEG has no alpha; flatten if needed
                if resized.mode in ("RGBA", "LA", "P"):
                    # Create white background for transparency
                    background = Image.new("RGB", resized.size, (255, 255, 255))
                    if resized.mode == "RGBA":
                        background.paste(resized, mask=resized.split()[-1])
                    else:
                        background.paste(
                            resized, mask=resized.split()[-1] if len(resized.split()) > 3 else None
                        )
                    resized = background
                resized.save(
                    handle, format="JPEG", quality=quality, optimize=True, progressive=True
                )
            elif output_format.name == "png":
                # PNG is lossless; ignore quality param
                resized.save(handle, format="PNG", optimize=True, compress_level=9)
            elif output_format.name == "tiff":
                resized.save(handle, format="TIFF", optimize=True)
            elif output_format.name == "bmp":
                resized.save(handle, format="BMP")
            elif output_format.name == "gif":
                resized.save(handle, format="GIF", optimize=True)
            else:
                # Fallback for other formats
                resized.save(handle)

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "input_format": input_format.name,
                "output_format": output_format.name,
                "original_size": image.size,
                "resized_size": resized.size,
                "fit_mode": fit,
                "sizing": sizing,
                "quality": quality,
            },
        )


def _resize_contain(image: Any, target_width: int, target_height: int) -> Any:
    """Resize image to fit within target dimensions, maintaining aspect ratio.

    Adds padding (letterbox/pillarbox) to achieve exact target dimensions.
    """
    from PIL import Image

    # Calculate the scale to fit within the target
    scale = min(target_width / image.width, target_height / image.height)
    new_width = int(image.width * scale)
    new_height = int(image.height * scale)

    # Resize the image
    resized = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # Create a background with the target dimensions
    background = Image.new("RGB", (target_width, target_height), (255, 255, 255))

    # Center the resized image on the background
    x_offset = (target_width - new_width) // 2
    y_offset = (target_height - new_height) // 2

    background.paste(resized, (x_offset, y_offset))
    return background


def _resize_cover(image: Any, target_width: int, target_height: int) -> Any:
    """Resize image to cover target dimensions, maintaining aspect ratio.

    Crops the image to achieve exact target dimensions.
    """
    from PIL import Image

    # Calculate the scale to cover the target
    scale = max(target_width / image.width, target_height / image.height)
    new_width = int(image.width * scale)
    new_height = int(image.height * scale)

    # Resize the image
    resized = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # Crop to target dimensions from center
    left = (new_width - target_width) // 2
    top = (new_height - target_height) // 2
    right = left + target_width
    bottom = top + target_height

    return resized.crop((left, top, right, bottom))


def _target_size(
    params: dict[str, Any], source_width: int, source_height: int
) -> tuple[int, int, str]:
    """The pixel size to produce, from whichever of the three forms was used.

    There are three ways to say how big, and a user should be able to pick the
    one they actually know:

    * ``scale`` -- a percentage of the original. The only form that needs no
      knowledge of the image at all, which is why it exists: asking someone
      for a pixel width they have never measured is asking them to leave and
      go find it.
    * one of ``width`` / ``height`` -- the other is computed from the image's
      own proportions, so "make it 800 wide" does not also require working out
      what 800 wide does to the height.
    * both -- the original behaviour, unchanged, with ``fit`` deciding how a
      differently-shaped image is made to fit that box.

    Returns the size and a short word naming which form produced it, so the
    result can report it rather than leaving the caller to guess.

    Rounding never yields zero: an image scaled to 1% is one pixel, not a
    file no viewer will open.
    """
    scale = _scale(params)
    width = _dimension(params, "width")
    height = _dimension(params, "height")

    if scale is not None:
        if width is not None or height is not None:
            raise InvalidParameterError(
                "scale and width/height say the same thing two different ways.",
                remedy="Give scale on its own, or give width and/or height instead.",
                context={"parameter": "scale"},
            )
        return (
            max(1, round(source_width * scale / 100)),
            max(1, round(source_height * scale / 100)),
            "scale",
        )

    if width is not None and height is not None:
        return width, height, "width+height"

    # One side given: the other follows from the image's own proportions, so
    # the result is never distorted and `fit` has nothing left to decide.
    if width is not None:
        return width, max(1, round(source_height * width / source_width)), "width"
    if height is not None:
        return max(1, round(source_width * height / source_height)), height, "height"

    raise InvalidParameterError(
        "A target size is required.",
        remedy=(
            "Give scale (a percentage of the original), or width, or height — "
            "any one is enough, and width with height sets an exact box."
        ),
        context={"parameter": "scale"},
    )


def _scale(params: dict[str, Any]) -> float | None:
    """Validate scale is a positive percentage, or ``None`` if not supplied."""
    value = params.get("scale")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidParameterError(
            f"scale must be a number, not {value!r}.",
            remedy="Use a percentage of the original, like 50 for half size.",
            context={"parameter": "scale"},
        )
    if value <= 0:
        raise InvalidParameterError(
            f"scale must be positive, not {value}.",
            remedy="Use a percentage of the original, like 50 for half size.",
            context={"parameter": "scale"},
        )
    return float(value)


def _dimension(params: dict[str, Any], name: str) -> int | None:
    """Validate one of width/height as a positive int, or ``None`` if absent.

    One function for both, because the two had identical bodies and identical
    messages differing only in the word -- which is how they drift.
    """
    value = params.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParameterError(
            f"{name} must be an integer, not {value!r}.",
            remedy=f"Use a positive whole number of pixels for the target {name}.",
            context={"parameter": name},
        )
    if value <= 0:
        raise InvalidParameterError(
            f"{name} must be positive, not {value}.",
            remedy=f"Use a positive whole number of pixels for the target {name}.",
            context={"parameter": name},
        )
    return value


def _fit(params: dict[str, Any]) -> str:
    """Validate fit is a valid mode, or raise InvalidParameterError."""
    value = params.get("fit", "cover")
    if not isinstance(value, str):
        raise InvalidParameterError(
            f"fit must be a string, not {value!r}.",
            remedy=f"Use one of: {', '.join(VALID_FIT_MODES)}",
            context={"parameter": "fit"},
        )
    if value not in VALID_FIT_MODES:
        raise InvalidParameterError(
            f"fit mode '{value}' is not recognized.",
            remedy=f"Use one of: {', '.join(VALID_FIT_MODES)}",
            context={"parameter": "fit"},
        )
    return value


def _quality(params: dict[str, Any]) -> int:
    """Validate quality is an int 1-95, or raise InvalidParameterError."""
    value = params.get("quality", 80)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParameterError(
            f"quality must be an integer (1-95), not {value!r}.",
            remedy="Use a number between 1 (smallest) and 95 (largest).",
            context={"parameter": "quality"},
        )
    if not (1 <= value <= 95):
        raise InvalidParameterError(
            f"quality {value} is outside the range 1-95.",
            remedy="Use a number between 1 (smallest) and 95 (largest).",
            context={"parameter": "quality"},
        )
    return value


def _version() -> str:
    """The version of Pillow actually doing the work.

    Best-effort: a version probe that failed must not fail a resize that
    already succeeded, so the result simply says less.
    """
    try:
        from importlib.metadata import version

        return f"Pillow/{version('Pillow')}"
    except Exception:
        return "Pillow/unknown"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return ResizeLocal()


__all__ = ["ResizeLocal", "build"]
