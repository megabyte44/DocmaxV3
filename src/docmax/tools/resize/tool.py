"""Metadata for ``resize``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. Pillow is not looked for here;
that happens in ``local.py``, which nobody touches until this tool runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

#: The fit modes, declared once. `local.py` validates against this rather
#: than keeping a second copy, so the dropdown a user sees and the values the
#: engine accepts cannot drift apart.
FIT_MODES = ("cover", "contain", "fill", "stretch")

#: The one mutually-exclusive choice this tool offers: say the size as a
#: percentage, or say it in pixels. Shared literally between every
#: `Param.group` below and what the form asks -- see its docstring.
RESIZE_METHOD = "Resize method"


def describe_inputs(paths: Sequence[Path]) -> str | None:
    """What the form says about the chosen image, before asking for a size.

    "1920 x 1080 pixels" is the fact a user needs in order to answer "what
    width?", and it is the one fact a terminal form could not previously tell
    them. Without it the width and height fields ask for a number the user has
    to leave the application to find.

    Best-effort and advisory: anything unreadable returns ``None`` and the
    form simply says nothing, because a description that failed must never be
    the reason a run cannot be started. `DocumentRef.from_path` and the engine
    remain the checks that actually gate one.

    Pillow is imported inside the function, as everywhere else in this
    package: this module is read on every ``--help``.
    """
    if not paths:
        return None
    try:
        from PIL import Image

        with Image.open(str(paths[0])) as image:
            width, height = image.size
    except Exception:
        return None
    return f"{paths[0].name} — {width} x {height} pixels"


SPEC = register(
    ToolSpec(
        name="resize",
        summary="Shrink or expand an image to new dimensions.",
        category="image",
        module=__name__.rpartition(".")[0],
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        output_required=False,
        default_suffix=".jpg",
        # Lets the form show the image's real dimensions the moment a path
        # is typed. Consumed generically by the TUI; no per-tool code there.
        describe_inputs=describe_inputs,
        params=(
            # Declared in the order a user decides them: which method, then
            # that method's own numbers, then the settings that apply either
            # way. The TUI renders one selector for "Resize method" and shows
            # only the fields for whichever answer is chosen -- see
            # `Param.group` in `core/registry.py`.
            Param(
                name="scale",
                label="scale (%)",
                description="Percent of the original size.",
                type_="float",
                group=RESIZE_METHOD,
                group_option="Percentage",
            ),
            Param(
                name="width",
                label="width (px)",
                description="Leave height blank to keep proportions.",
                type_="int",
                group=RESIZE_METHOD,
                group_option="Dimensions",
            ),
            Param(
                name="height",
                label="height (px)",
                description="Leave width blank to keep proportions.",
                type_="int",
                group=RESIZE_METHOD,
                group_option="Dimensions",
            ),
            Param(
                name="fit",
                description="How to fill a box that isn't the image's own shape.",
                type_="str",
                default="cover",
                # The four values were already the only ones `local.py`
                # accepts; declaring them here is what turns a free-text box
                # into a dropdown, with no change to what the engine does
                # with them.
                choices=FIT_MODES,
                group=RESIZE_METHOD,
                group_option="Dimensions",
            ),
            Param(
                name="quality",
                description="JPEG/WEBP only. PNG is always lossless.",
                type_="int",
                default=80,
            ),
        ),
    )
)

__all__ = ["FIT_MODES", "SPEC", "describe_inputs"]
