"""Metadata for ``compress-image``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. Pillow is not looked for here;
that happens in ``local.py``, which nobody touches until this tool runs.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

SPEC = register(
    ToolSpec(
        name="compress-image",
        summary="Shrink an image file while preserving its format.",
        category="image",
        module=__name__.rpartition(".")[0],
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        default_suffix=".jpg",
        params=(
            Param(
                name="quality",
                description="JPEG/WEBP quality (1-95). PNG uses lossless optimization and ignores this.",
                type_="int",
                default=80,
            ),
        ),
    )
)

__all__ = ["SPEC"]
