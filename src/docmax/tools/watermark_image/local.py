"""The local engine for ``watermark-image``.

## How the text gets onto the image

An overlay layer with transparent background is created in memory holding
nothing but text drawn with the given opacity, angle, position, and size.
The overlay is then composited over the original image using PIL's alpha_composite.
This keeps the operation pure Python with no external binaries, and means the
original image content is untouched underneath.

The font used is a default TrueType font that PIL provides; if unavailable,
PIL falls back to a bitmap font. Font selection is handled entirely by PIL.

## What this is not

A watermark drawn over content can be removed by anyone with an image editor: it
is a composite layer, not a change to the image data beneath it. This tool marks
images; it does not protect them.

Pillow is imported inside the methods, not at module scope.
"""

from __future__ import annotations

import importlib.util
import math
from typing import TYPE_CHECKING, Any

from docmax.core.branding import DIST_NAME
from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _formats, _position

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

#: Installed by the ``images`` extra. ``PIL`` is the import name of Pillow.
DEPENDENCY = "PIL"


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


class WatermarkImageLocal:
    """Draw text over an image file in place, with Pillow."""

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
        """Watermark ``docs[0]`` into ``target``."""
        import time

        from PIL import Image, ImageDraw, ImageFont

        from docmax.core.atomic import atomic_write
        from docmax.tools.watermark_image.validators import is_readable_image

        if not docs:
            raise InvalidParameterError(
                "Watermark-image needs a document.",
                remedy="Pass the image to watermark.",
            )

        # Look up the input format
        input_format = _formats.image_for_suffix(docs[0].suffix)
        if input_format is None or input_format.rasterise_flag is None:
            raise InvalidParameterError(
                f"{docs[0].path.name} is not a format watermark-image handles.",
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

        text = _text(params)
        position = _position.canonical(params.get("position") or "center")
        size = _positive(params, "size", 48.0)
        opacity = _opacity(params)
        angle = _angle(params)

        started = time.monotonic()
        image = Image.open(str(docs[0].path))
        # Ensure image is in RGB mode for consistent handling
        if image.mode != "RGB":
            if image.mode == "RGBA":
                # Composite RGBA onto white background
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                image = background  # type: ignore[assignment]
            else:
                image = image.convert("RGB")  # type: ignore[assignment]

        img_width, img_height = image.size
        progress.start("Watermarking image", total=None)
        cancellation.raise_if_cancelled(operation="watermark-image")

        # Create overlay layer with transparency
        overlay = Image.new("RGBA", (img_width, img_height), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)

        # Determine font size in pixels (PIL uses pixels, not points)
        # Rough conversion: 72 points = 96 pixels (typical screen DPI)
        font_size_pixels = int(size * 96 / 72)

        # Try to get a TrueType font; fall back to default if not available
        try:
            font = ImageFont.load_default(size=font_size_pixels)
        except (TypeError, AttributeError):
            # Fallback for older PIL versions
            font = ImageFont.load_default()

        # Get text bounding box to calculate dimensions
        bbox = overlay_draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Calculate position based on the grid
        x, y = _position.place(
            position,
            page_width=img_width,
            page_height=img_height,
            content_width=text_width,
            content_height=text_height,
            margin=36.0,  # Same default margin as PDF watermark
        )

        # Draw text with semi-transparency
        # Convert opacity to alpha (0-255)
        alpha = int(opacity * 255)
        text_color = (128, 128, 128, alpha)  # Grey with alpha

        # For rotation, we need to create a rotated text layer
        if angle != 0:
            # Create a temporary image for rotated text
            # Make it larger to accommodate rotation
            diagonal = math.sqrt(text_width**2 + text_height**2)
            temp_size = int(diagonal * 1.5) + 10
            temp_overlay = Image.new("RGBA", (temp_size, temp_size), (0, 0, 0, 0))
            temp_draw = ImageDraw.Draw(temp_overlay)

            # Draw text in center of temp image
            center_x = temp_size / 2
            center_y = temp_size / 2
            temp_draw.text(
                (center_x - text_width / 2, center_y - text_height / 2),
                text,
                font=font,
                fill=text_color,
            )

            # Rotate the temporary image
            rotated = temp_overlay.rotate(-angle, expand=False, resample=Image.Resampling.BICUBIC)

            # Composite the rotated text onto the main overlay at the calculated position
            # Adjust position to account for rotation
            paste_x = int(x - (rotated.width - text_width) / 2)
            paste_y = int(y - (rotated.height - text_height) / 2)
            overlay.paste(rotated, (paste_x, paste_y), rotated)
        else:
            # No rotation, draw directly
            overlay_draw.text((x, y), text, font=font, fill=text_color)

        # Composite overlay onto the original image
        image_with_watermark = Image.alpha_composite(image.convert("RGBA"), overlay)
        # Convert back to RGB for output
        image_with_watermark = image_with_watermark.convert("RGB")

        with atomic_write(target, validators=(is_readable_image(output_format.name),)) as handle:
            if output_format.name == "jpeg":
                image_with_watermark.save(
                    handle, format="JPEG", quality=85, optimize=True, progressive=True
                )
            elif output_format.name == "png":
                image_with_watermark.save(handle, format="PNG", optimize=True, compress_level=9)
            elif output_format.name == "tiff":
                image_with_watermark.save(handle, format="TIFF", optimize=True)
            elif output_format.name == "bmp":
                image_with_watermark.save(handle, format="BMP")
            elif output_format.name == "gif":
                image_with_watermark.save(handle, format="GIF", optimize=True)
            else:
                # Fallback for unknown formats
                image_with_watermark.save(handle)

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "format": output_format.name,
                "text": text,
                "position": position,
                "size": size,
                "opacity": opacity,
                "angle": angle,
                "image_width": img_width,
                "image_height": img_height,
            },
        )


def _text(params: dict[str, Any]) -> str:
    """Validate text is provided and not empty, or raise InvalidParameterError."""
    text_value = params.get("text", "")
    if not isinstance(text_value, str):
        text_value = str(text_value)
    value = text_value.strip()
    if not value:
        raise InvalidParameterError(
            "text cannot be empty.",
            remedy="Provide text to draw on the image.",
            context={"parameter": "text"},
        )
    return value


def _positive(params: dict[str, Any], name: str, default: float) -> float:
    """Validate a parameter is a positive number, or raise InvalidParameterError."""
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidParameterError(
            f"{name} must be a number, not {value!r}.",
            remedy=f"Use a positive number for {name}.",
            context={"parameter": name},
        )
    num = float(value)
    if num <= 0:
        raise InvalidParameterError(
            f"{name} {num} must be positive.",
            remedy=f"Use a number greater than 0 for {name}.",
            context={"parameter": name},
        )
    return num


def _opacity(params: dict[str, Any]) -> float:
    """Validate opacity is 0-1, or raise InvalidParameterError."""
    value = params.get("opacity", 0.15)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidParameterError(
            f"opacity must be a number, not {value!r}.",
            remedy="Use a number between 0 (invisible) and 1 (solid).",
            context={"parameter": "opacity"},
        )
    num = float(value)
    if not (0 <= num <= 1):
        raise InvalidParameterError(
            f"opacity {num} is outside the range 0-1.",
            remedy="Use a number between 0 (invisible) and 1 (solid).",
            context={"parameter": "opacity"},
        )
    return num


def _angle(params: dict[str, Any]) -> float:
    """Validate angle is a reasonable number, or raise InvalidParameterError."""
    value = params.get("angle", 45.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidParameterError(
            f"angle must be a number, not {value!r}.",
            remedy="Use a number in degrees (e.g., 0-360).",
            context={"parameter": "angle"},
        )
    # Normalize angle to 0-360 range for consistency
    # but allow any value since rotation wraps naturally
    return float(value)


def _version() -> str:
    """The version of Pillow actually doing the work.

    Best-effort: a version probe that failed must not fail a watermark that
    already succeeded, so the result simply says less.
    """
    try:
        from importlib.metadata import version

        return f"Pillow/{version('Pillow')}"
    except Exception:
        return "Pillow/unknown"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return WatermarkImageLocal()


__all__ = ["WatermarkImageLocal", "build"]
