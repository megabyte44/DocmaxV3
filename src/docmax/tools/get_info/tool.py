"""Metadata for ``get-info``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. The pypdf import lives in ``local.py``,
which nobody touches until this tool is actually run.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import ToolSpec, register

SPEC = register(
    ToolSpec(
        name="get-info",
        summary="Report page count, size, encryption and metadata.",
        category="inspect",
        module=__name__.rpartition(".")[0],
        # Pure pypdf: uploading a document to perform a local, millisecond-long
        # operation would be slower, less private, and would need a network.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        # Read-only: the answer travels in ToolResult.details. See ADR 0036.
        produces_output=False,
    )
)

__all__ = ["SPEC"]
