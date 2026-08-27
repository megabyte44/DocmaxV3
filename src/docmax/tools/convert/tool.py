"""Metadata for ``convert``.

Imported during discovery -- on every ``--help`` -- so it imports nothing but
``core`` and the format table, and does no work at import time. Pandoc is not
looked for here; that happens in ``local.py``, which nobody touches until this
tool runs.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register
from docmax.tools._formats import convertible_names

SPEC = register(
    ToolSpec(
        name="convert",
        summary="Convert a document to another format with Pandoc.",
        category="convert",
        module=__name__.rpartition(".")[0],
        # Cloud lands at M6, and `convert` is one of the five tools that will
        # get it -- installing Pandoc and a LaTeX distribution is exactly the
        # pain cloud exists to remove. But the engine does not exist yet, and
        # declaring it now would make the router offer something that cannot
        # run. Same reasoning as `compress` at M3.
        supported_engines=frozenset({Engine.LOCAL}),
        accepts_multiple_inputs=False,
        # Unused: the CLI requires `-o`, so a destination is never derived. It
        # stays at the default rather than becoming parameter-dependent, which
        # would be a change to ToolSpec and OutputTarget -- see ADR 0011.
        default_suffix=".pdf",
        params=(
            Param(
                name="to",
                description="The format to convert to.",
                type_="str",
                required=True,
                # Read from the shared table, never retyped. ADR 0010.
                choices=convertible_names(),
            ),
            Param(
                name="standalone",
                description="Produce a complete document rather than a fragment.",
                type_="bool",
                default=True,
            ),
        ),
    )
)

__all__ = ["SPEC"]
