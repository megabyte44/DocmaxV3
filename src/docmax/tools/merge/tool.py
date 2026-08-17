"""Metadata for ``merge``.

This module is imported during discovery — every time the tool list is built, on
every ``--help``. So it imports nothing but ``core`` and does no work at import
time. The pypdf import lives in ``local.py``, which nobody touches until this
tool is actually run.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

SPEC = register(
    ToolSpec(
        name="merge",
        summary="Combine several PDFs into one, in the order given.",
        category="assemble",
        module=__name__.rpartition(".")[0],
        # No cloud engine, deliberately. See the module docstring.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=True,
        default_suffix=".pdf",
        params=(
            Param(
                name="outline",
                description="Add a bookmark per source file, named after it.",
                type_="bool",
                default=True,
            ),
        ),
    )
)

__all__ = ["SPEC"]
