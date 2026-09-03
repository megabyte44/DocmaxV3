"""Metadata for ``watermark-image``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. Pillow is not looked for here;
that happens in ``local.py``, which nobody touches until this tool runs.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register
from docmax.tools._position import NAMES as POSITIONS

SPEC = register(
    ToolSpec(
        name="watermark-image",
        summary="Draw semi-transparent text on an image.",
        category="image",
        module=__name__.rpartition(".")[0],
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        output_required=False,
        default_suffix=".jpg",
        params=(
            Param(
                name="text",
                description="The words to draw.",
                type_="str",
                required=True,
            ),
            Param(
                name="position",
                description="Which of the nine cells the text sits in.",
                type_="str",
                default="center",
                choices=POSITIONS,
            ),
            Param(
                name="size",
                description="Font size in points.",
                type_="float",
                default=48.0,
            ),
            Param(
                name="opacity",
                description="How opaque, from 0 (invisible) to 1 (solid).",
                type_="float",
                default=0.15,
            ),
            Param(
                name="angle",
                description="Degrees clockwise from horizontal.",
                type_="float",
                default=45.0,
            ),
        ),
    )
)

__all__ = ["SPEC"]
