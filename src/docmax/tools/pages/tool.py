"""Metadata for ``pages``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. The pypdf import lives in ``local.py``,
which nobody touches until this tool is actually run.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

SPEC = register(
    ToolSpec(
        name="pages",
        summary="Keep or delete selected pages.",
        category="edit",
        module=__name__.rpartition(".")[0],
        # Pure pypdf: uploading a document to perform a local, millisecond-long
        # operation would be slower, less private, and would need a network.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        default_suffix=".pdf",
        params=(
            Param(
                name="select",
                description="Pages to keep, e.g. 1-3,7. Mutually exclusive with delete.",
                type_="str",
                default=None,
            ),
            Param(
                name="delete",
                description="Pages to remove, e.g. 4. Mutually exclusive with select.",
                type_="str",
                default=None,
            ),
        ),
    )
)

__all__ = ["SPEC"]
