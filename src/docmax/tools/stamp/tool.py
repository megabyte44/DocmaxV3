"""Metadata for ``stamp``.

Imported during discovery -- on every ``--help`` -- so it imports nothing but
``core`` and does no work at import time. The pypdf import lives in ``local.py``,
which nobody touches until this tool is actually run.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register
from docmax.tools._position import NAMES as POSITIONS

SPEC = register(
    ToolSpec(
        name="stamp",
        summary="Draw another PDF's first page onto every page.",
        category="mark",
        module=__name__.rpartition(".")[0],
        # Pure pypdf: uploading a document to perform a local, millisecond-long
        # operation would be slower, less private, and would need a network.
        supported_engines=frozenset({Engine.LOCAL}),
        # The overlay is the second input, not a parameter. `local.py` explains
        # what that buys, and it is the whole reason this is not a `--stamp
        # path` string: an OutputTarget only guards the inputs it is given.
        accepts_multiple_inputs=True,
        default_suffix=".pdf",
        params=(
            Param(
                name="position",
                description="Which of the nine cells the stamp sits in.",
                type_="str",
                default="bottom-right",
                choices=POSITIONS,
            ),
            Param(
                name="scale",
                description="Resize the stamp before placing it. 1 is its own size.",
                type_="float",
                default=1.0,
            ),
            Param(
                name="pages",
                description="Which pages to stamp, e.g. 1-3,7. Default: all.",
                type_="str",
            ),
        ),
    )
)

__all__ = ["SPEC"]
