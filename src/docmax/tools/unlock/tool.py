"""Metadata for ``unlock``.

Imported during discovery -- on every ``--help`` -- so it imports nothing but
``core`` and does no work at import time. The pypdf import lives in ``local.py``,
which nobody touches until this tool is actually run.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

SPEC = register(
    ToolSpec(
        name="unlock",
        summary="Write a copy with the password removed.",
        category="secure",
        module=__name__.rpartition(".")[0],
        # Pure pypdf. A cloud engine would mean uploading a document *and* its
        # password, which is a worse arrangement than the problem it solves.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        default_suffix=".pdf",
        params=(
            Param(
                name="password",
                description="A password that already opens the document.",
                type_="str",
            ),
        ),
    )
)

__all__ = ["SPEC"]
