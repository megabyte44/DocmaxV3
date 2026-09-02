"""The local engine for ``compress-image``.

Compressing an image file in place, preserving its format. This is a pure-Python
operation using Pillow; no external binary is needed, and no cloud engine makes
sense — a millisecond-long operation per ADR 0034.

Format preservation is enforced: compressing a JPEG stays a JPEG. A user who
wants to convert and compress uses two tools.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.branding import DIST_NAME
from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _formats

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


class CompressImageLocal:
    """Shrink an image file in place, preserving its format, with Pillow."""

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
        """Compress ``docs[0]`` into ``target``, preserving format."""
        import time

        from PIL import Image

        from docmax.core.atomic import atomic_write
        from docmax.tools.compress_image.validators import is_readable_image

        if not docs:
            raise InvalidParameterError(
                "Compress-image needs a document.",
                remedy="Pass the image to compress.",
            )

        # Look up the input format
        input_format = _formats.image_for_suffix(docs[0].suffix)
        if input_format is None or input_format.rasterise_flag is None:
            raise InvalidParameterError(
                f"{docs[0].path.name} is not a format compress-image handles.",
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

        # Enforce format preservation
        if input_format.name != output_format.name:
            raise InvalidParameterError(
                f"compress-image preserves format; input is {input_format.name}, "
                f"but -o names {output_format.name}. Use convert to change format.",
                remedy=f"Use the same format: -o file.{input_format.suffixes[0].lstrip('.')}. Or use `convert` to change formats.",
                context={
                    "input_format": input_format.name,
                    "output_format": output_format.name,
                },
            )

        quality = _quality(params)
        document = docs[0]
        original_bytes = document.size_bytes
        started = time.monotonic()

        image = Image.open(str(document.path))

        progress.start(f"Compressing {image.format or 'image'}", total=None)
        cancellation.raise_if_cancelled(operation="compress-image")

        with atomic_write(target, validators=(is_readable_image(input_format.name),)) as handle:
            if input_format.name == "jpeg":
                # JPEG has no alpha; flatten if needed
                if image.mode in ("RGBA", "LA", "P"):
                    # Create white background for transparency
                    background = Image.new("RGB", image.size, (255, 255, 255))
                    if image.mode == "RGBA":
                        background.paste(image, mask=image.split()[-1])
                    else:
                        background.paste(
                            image, mask=image.split()[-1] if len(image.split()) > 3 else None
                        )
                    image = background
                image.save(handle, format="JPEG", quality=quality, optimize=True, progressive=True)
            elif input_format.name == "png":
                # PNG is lossless; ignore quality param
                image.save(handle, format="PNG", optimize=True, compress_level=9)
            elif input_format.name == "tiff":
                image.save(handle, format="TIFF", optimize=True)
            elif input_format.name == "bmp":
                image.save(handle, format="BMP")
            elif input_format.name == "gif":
                image.save(handle, format="GIF", optimize=True)

        compressed_bytes = target.destination.stat().st_size
        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "format": input_format.name,
                "quality": quality,
                "original_bytes": original_bytes,
                "compressed_bytes": compressed_bytes,
                # Reported rather than judged. Compression can make a file
                # *larger* — an already-optimised image re-encoded at a different
                # quality does — and saying so is more useful than hiding it.
                "saved_bytes": original_bytes - compressed_bytes,
            },
        )


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

    Best-effort: a version probe that failed must not fail a compression that
    already succeeded, so the result simply says less.
    """
    try:
        from importlib.metadata import version

        return f"Pillow/{version('Pillow')}"
    except Exception:
        return "Pillow/unknown"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return CompressImageLocal()


__all__ = ["CompressImageLocal", "build"]
