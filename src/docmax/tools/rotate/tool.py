"""Metadata for ``rotate``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. The pypdf import lives in ``local.py``,
which nobody touches until this tool is actually run.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

SPEC = register(
    ToolSpec(
        name="rotate",
        summary="Rotate pages by a multiple of 90 degrees.",
        category="edit",
        module=__name__.rpartition(".")[0],
        # Pure pypdf: uploading a document to perform a local, millisecond-long
        # operation would be slower, less private, and would need a network.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        default_suffix=".pdf",
        params=(
            Param(
                name="by",
                description="Degrees clockwise: 90, 180 or 270.",
                type_="int",
                default=90,
            ),
            Param(
                name="pages",
                description="Which pages to rotate, e.g. 1-3,7. Default: all.",
                type_="str",
                default=None,
            ),
        ),
    )
)

__all__ = ["SPEC"]
