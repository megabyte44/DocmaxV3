"""Metadata for ``watermark``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. The pypdf import lives in ``local.py``,
which nobody touches until this tool is actually run.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register
from docmax.tools._position import NAMES as POSITIONS

SPEC = register(
    ToolSpec(
        name="watermark",
        summary="Draw text across every page.",
        category="mark",
        module=__name__.rpartition(".")[0],
        # Pure pypdf: uploading a document to perform a local, millisecond-long
        # operation would be slower, less private, and would need a network.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        default_suffix=".pdf",
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
                description="Degrees anticlockwise from horizontal.",
                type_="float",
                default=45.0,
            ),
            Param(
                name="pages",
                description="Which pages to mark, e.g. 1-3,7. Default: all.",
                type_="str",
            ),
        ),
    )
)

__all__ = ["SPEC"]
