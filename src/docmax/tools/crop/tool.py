"""Metadata for ``crop``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. The pypdf import lives in ``local.py``,
which nobody touches until this tool is actually run.

``crop`` exists because [ADR 0005](../../../docs/adr/0005-gui-pickers.md) named
it as one of the three operations whose parameter is genuinely unguessable
without seeing the page, and because that ADR's rule is that **the headless form
ships first**. ``--box`` is that form; the picker only fills it in.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

SPEC = register(
    ToolSpec(
        name="crop",
        summary="Trim every page to a rectangle.",
        category="edit",
        module=__name__.rpartition(".")[0],
        # Pure pypdf: cropping rewrites two rectangles in the page dictionary.
        # Uploading a document to do that would be slower, less private, and
        # would need a network.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        default_suffix=".pdf",
        params=(
            Param(
                name="box",
                description=(
                    "The rectangle to keep, as x,y,width,height in points, "
                    "measured from the bottom-left of the page."
                ),
                type_="str",
                default=None,
                required=True,
            ),
        ),
    )
)

__all__ = ["SPEC"]
