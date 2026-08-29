"""Metadata for ``split``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. The pypdf import lives in ``local.py``,
which nobody touches until this tool is actually run.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

SPEC = register(
    ToolSpec(
        name="split",
        summary="Split a PDF into several files.",
        category="assemble",
        module=__name__.rpartition(".")[0],
        # Pure pypdf: uploading a document to perform a local, millisecond-long
        # operation would be slower, less private, and would need a network.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        # `-o` names a directory the parts are written into, not a file — see
        # ADR 0003's `atomic_dir` and ADR 0031.
        produces_directory=True,
        params=(
            Param(
                name="every",
                description="Pages per output file. 1 gives one file per page.",
                type_="int",
                default=1,
            ),
            Param(
                name="pages",
                description="Which pages to split, e.g. 1-3,7. Default: all.",
                type_="str",
                default=None,
            ),
        ),
    )
)

__all__ = ["SPEC"]
