"""The local engine for ``convert-image``.

Converting an image from one format to another. This is a pure-Python operation
using Pillow; no external binary is needed, and no cloud engine makes sense — a
millisecond-long operation per ADR 0034.

Unlike ``compress-image``, which preserves format, ``convert-image`` allows any
readable image format as input and any writable image format as output. This is
the tool's entire purpose.
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

#: JPEG quality used for every conversion that writes JPEG. Fixed rather than
#: a parameter: this tool's job is changing format, not tuning size against
#: quality -- that tradeoff belongs to compress-image, which a converted file
#: can be piped through next. High enough that the conversion itself is not
#: the lossy step.
_JPEG_QUALITY = 95


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


class ConvertImageLocal:
    """Convert an image from one format to another with Pillow."""

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
        """Convert ``docs[0]`` to ``target`` in the specified output format."""
        import time

        from PIL import Image

        from docmax.core.atomic import atomic_write
        from docmax.tools.convert_image.validators import is_readable_image

        if not docs:
            raise InvalidParameterError(
                "Convert-image needs a document.",
                remedy="Pass the image to convert.",
            )

        # Look up the input format
        input_format = _formats.image_for_suffix(docs[0].suffix)
        if input_format is None:
            raise InvalidParameterError(
                f"{docs[0].path.name} is not a recognized image format.",
                remedy=f"Supported input formats: {', '.join(_formats.readable_image_names())}.",
                context={"format": docs[0].suffix},
            )

        # `--to` is the authority when it is given: the user named the format
        # outright, and a filename extension is not a second opinion about it.
        # `ToolSpec.suffix_for_params` has normally corrected the destination
        # to match before this runs, so the two agree and the file is not
        # mislabelled -- but the format written is decided here, by the
        # parameter, whether or not that correction happened. That matters for
        # any caller reaching the strategy directly, and it is what makes
        # "--to png produces a PNG" true unconditionally rather than only
        # along the router's path.
        requested = _requested_format(params)
        if requested is not None:
            output_format = requested
        else:
            # No `--to`: the destination's extension decides, as before.
            found = _formats.image_for_suffix(target.destination.suffix)
            if found is None:
                raise InvalidParameterError(
                    f"{target.destination.name} is not a recognized image format.",
                    remedy=(
                        f"Supported output formats: {', '.join(_formats.readable_image_names())}."
                    ),
                    context={"format": target.destination.suffix},
                )
            output_format = found

        document = docs[0]
        started = time.monotonic()

        image = Image.open(str(document.path))

        progress.start(f"Converting {input_format.name} to {output_format.name}", total=None)
        cancellation.raise_if_cancelled(operation="convert-image")

        with atomic_write(target, validators=(is_readable_image(output_format.name),)) as handle:
            if output_format.name == "jpeg":
                # JPEG has no alpha channel; flatten any transparency to white background
                if image.mode in ("RGBA", "LA", "P"):
                    # Create white background for transparency
                    background = Image.new("RGB", image.size, (255, 255, 255))
                    if image.mode == "RGBA":
                        background.paste(image, mask=image.split()[-1])
                    else:
                        background.paste(
                            image, mask=image.split()[-1] if len(image.split()) > 3 else None
                        )
                    image = background  # type: ignore[assignment]
                elif image.mode != "RGB":
                    # Convert to RGB for JPEG compatibility
                    image = image.convert("RGB")  # type: ignore[assignment]
                image.save(
                    handle, format="JPEG", quality=_JPEG_QUALITY, optimize=True, progressive=True
                )
            elif output_format.name == "png":
                # PNG supports alpha; convert but preserve transparency
                if image.mode not in ("RGB", "RGBA", "LA", "L", "1"):
                    image = image.convert("RGBA")  # type: ignore[assignment]
                image.save(handle, format="PNG", optimize=True, compress_level=9)
            elif output_format.name == "tiff":
                # TIFF can support multiple modes
                image.save(handle, format="TIFF", optimize=True)
            elif output_format.name == "bmp":
                # BMP typically works with RGB
                if image.mode not in ("RGB", "L", "1"):
                    image = image.convert("RGB")  # type: ignore[assignment]
                image.save(handle, format="BMP")
            elif output_format.name == "gif":
                # GIF has limited color support; convert to palette mode if needed
                if image.mode not in ("P", "L", "1"):
                    image = image.convert("P")  # type: ignore[assignment]
                image.save(handle, format="GIF", optimize=True)

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "input_format": input_format.name,
                "output_format": output_format.name,
                "requested_format": (requested.name if requested is not None else None),
            },
        )


def _requested_format(params: dict[str, Any]) -> _formats.ImageFormat | None:
    """The format ``--to`` asked for, or ``None`` when it was not supplied.

    Routed through ``_formats.image`` rather than compared as a string, so an
    unknown name produces the same typed error, listing the same vocabulary,
    that every other consumer of the table produces. ADR 0010.
    """
    value = params.get("to")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidParameterError(
            f"--to must name an image format, not {value!r}.",
            remedy=f"Choose one of: {', '.join(_formats.readable_image_names())}.",
            context={"parameter": "to"},
        )
    try:
        return _formats.image(value.strip())
    except InvalidParameterError as exc:
        # The lookup is the shared table's, and stays that way -- but its
        # remedy names the formats `to-images` can *rasterise*, which is the
        # narrower list. convert-image writes every declared raster format, so
        # only the suggestion is restated, against the vocabulary this tool
        # actually accepts. Re-raised rather than pre-checked so there remains
        # exactly one place that decides whether a name is a format.
        raise InvalidParameterError(
            str(exc.message),
            remedy=f"Choose one of: {', '.join(_formats.readable_image_names())}.",
            context={"parameter": "to", "format": value.strip()},
        ) from exc


def _version() -> str:
    """The version of Pillow actually doing the work.

    Best-effort: a version probe that failed must not fail a conversion that
    already succeeded, so the result simply says less.
    """
    try:
        from importlib.metadata import version

        return f"Pillow/{version('Pillow')}"
    except Exception:
        return "Pillow/unknown"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return ConvertImageLocal()


__all__ = ["ConvertImageLocal", "build"]
