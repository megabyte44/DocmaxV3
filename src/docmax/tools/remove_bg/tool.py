"""Metadata for ``remove-bg``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. The rembg import lives in ``local.py``,
which nobody touches until this tool is actually run.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

SPEC = register(
    ToolSpec(
        name="remove-bg",
        summary="Remove the background from an image, producing a transparent PNG.",
        category="image",
        module=__name__.rpartition(".")[0],
        # Pure rembg: an ONNX model download at first use, local-only per ADR 0012.
        # Cloud engine is explicitly deferred; this is local-only for now.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        # Always PNG for transparency. Unlike ``convert``, this is never wrong,
        # so no ``output_required`` is needed — ``.png`` is always correct.
        default_suffix=".png",
        params=(
            Param(
                name="model",
                description="The ONNX model to use for background removal.",
                type_="str",
                default="u2net",
                choices=("u2net", "u2netp", "isnet-general-use"),
            ),
        ),
    )
)

__all__ = ["SPEC"]
