"""Metadata for ``compress``.

Imported during discovery — on every ``--help`` — so it imports nothing but
``core`` and does no work at import time. Ghostscript is not looked for here;
that happens in ``local.py``, which nobody touches until this tool runs.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

#: Ghostscript's own presets, exposed under its names rather than invented ones.
#: A user who knows `/ebook` should not have to learn a synonym, and anyone
#: reading the Ghostscript manual should recognise what DocMax passed it.
PRESETS = ("screen", "ebook", "printer", "prepress", "default")

SPEC = register(
    ToolSpec(
        name="compress",
        summary="Shrink a PDF with Ghostscript.",
        category="optimise",
        module=__name__.rpartition(".")[0],
        # Both, since M6. Installing Ghostscript is exactly the pain cloud
        # exists to remove, and `cloud.py` now implements it — the condition M3
        # set for declaring this: never offer an engine that cannot run.
        supported_engines=frozenset({Engine.LOCAL, Engine.CLOUD}),
        accepts_multiple_inputs=False,
        default_suffix=".pdf",
        params=(
            Param(
                name="preset",
                description="Ghostscript quality preset.",
                type_="str",
                default="ebook",
                choices=PRESETS,
            ),
        ),
    )
)

__all__ = ["PRESETS", "SPEC"]
