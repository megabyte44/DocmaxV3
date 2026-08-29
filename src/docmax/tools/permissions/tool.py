"""Metadata for ``permissions``.

Imported during discovery -- on every ``--help`` -- so it imports nothing but
``core`` and does no work at import time. The pypdf import lives in ``local.py``,
which nobody touches until this tool is actually run.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

SPEC = register(
    ToolSpec(
        name="permissions",
        summary="Report what a PDF says a reader may do with it.",
        category="inspect",
        module=__name__.rpartition(".")[0],
        # Pure pypdf, and read-only besides. There is nothing here a network
        # could make faster.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        params=(
            Param(
                name="password",
                description="Needed if the document is encrypted.",
                type_="str",
            ),
        ),
    )
)

__all__ = ["SPEC"]
