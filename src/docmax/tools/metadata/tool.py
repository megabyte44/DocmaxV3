"""Metadata for ``metadata``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. The pypdf import lives in ``local.py``,
which nobody touches until this tool is actually run.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

SPEC = register(
    ToolSpec(
        name="metadata",
        summary="Read or set document metadata.",
        category="inspect",
        module=__name__.rpartition(".")[0],
        # Pure pypdf: uploading a document to perform a local, millisecond-long
        # operation would be slower, less private, and would need a network.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        default_suffix=".pdf",
        # Writes only when asked to (`--set`/`--clear`); `cli/commands.py`
        # still decides *whether* `-o` is required for a given invocation,
        # since that depends on other parameters `ToolSpec` cannot see. But no
        # destination may ever be implied for a write -- editing the source in
        # place is exactly what this tool promises never to do -- and that
        # fact is unconditional. See ADR 0033.
        output_required=True,
        params=(
            Param(
                name="set",
                description="Fields to write, as Title=... pairs. Omit to read.",
                type_="str",
                default=None,
            ),
            Param(
                name="clear",
                description="Remove every metadata field before writing.",
                type_="bool",
                default=False,
            ),
        ),
    )
)

__all__ = ["SPEC"]
