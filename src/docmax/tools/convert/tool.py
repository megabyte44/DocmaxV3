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
        # Both, since M6. Installing Pandoc is exactly the pain cloud exists to
        # remove, and `cloud.py` now implements it.
        #
        # The cloud engine does not widen what `convert` accepts: ADR 0011's
        # format boundary is the shared `_formats` table, which both engines
        # validate against, so `--to pdf` is refused on either path. An endpoint
        # with a LaTeX distribution installed still will not be asked for one.
        supported_engines=frozenset({Engine.LOCAL, Engine.CLOUD}),
        accepts_multiple_inputs=False,
        # Never trusted to name a real destination: the true extension is
        # `to`, a parameter, and Pandoc can never write PDF (ADR 0011) -- so
        # `.pdf` is not merely a default that might be wrong sometimes, it is
        # wrong every time. Left at the historical default rather than made
        # parameter-dependent, which is still the open "output extension
        # depends on a parameter" seam (docs/planning/current-status.md).
        default_suffix=".pdf",
        # What *is* decided, narrowly: no destination may ever be implied for
        # this tool, in any interface. The CLI already enforced that with a
        # required `-o`; this is what lets `tui/app.py` know it too, instead
        # of silently diverging the way issue #24 found. See ADR 0033.
        output_required=True,
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
