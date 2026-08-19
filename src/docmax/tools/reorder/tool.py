"""Metadata for ``reorder``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. The pypdf import lives in ``local.py``,
which nobody touches until this tool is actually run.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

SPEC = register(
    ToolSpec(
        name="reorder",
        summary="Reorder pages into a given sequence.",
        category="edit",
        module=__name__.rpartition(".")[0],
        # Pure pypdf: uploading a document to perform a local, millisecond-long
        # operation would be slower, less private, and would need a network.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        default_suffix=".pdf",
        params=(
            Param(
                name="order",
                description="The new order, e.g. 3,1,2. Must list every page exactly once.",
                type_="str",
                default=None,
                required=True,
            ),
        ),
    )
)

__all__ = ["SPEC"]
