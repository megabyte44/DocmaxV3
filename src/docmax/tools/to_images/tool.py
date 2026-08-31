"""Metadata for ``to-images``.

Imported during discovery -- on every ``--help`` -- so it imports nothing but
``core`` and the format table, and does no work at import time. Poppler is not
looked for here; that happens in ``local.py`` and ``cloud.py``.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register
from docmax.tools._formats import rasterisable_names

SPEC = register(
    ToolSpec(
        name="to-images",
        summary="Render each page of a PDF as an image.",
        category="convert",
        module=__name__.rpartition(".")[0],
        # Cloud engine since ADR 0034: `to-images` needs the exact binary `ocr`
        # does -- Poppler's `pdftoppm`, `used_by=("ocr", "to-images")` in
        # `tools/_binaries.py` -- and cloud already exists to remove that
        # install for one of the two tools that need it.
        supported_engines=frozenset({Engine.LOCAL, Engine.CLOUD}),
        accepts_multiple_inputs=False,
        # `default_suffix` is otherwise unused: the CLI requires `-o`, so a
        # destination is never derived. `-o` itself names a directory the
        # images are written into, not a file — `split` has the same shape.
        # ToolSpec now has a way to say so; see ADR 0031.
        default_suffix=".pdf",
        produces_directory=True,
        params=(
            Param(
                name="format",
                description="Image format to write.",
                type_="str",
                default="png",
                # Read from the shared table, never retyped. ADR 0010.
                choices=rasterisable_names(),
            ),
            Param(
                name="dpi",
                description="Resolution in dots per inch. Higher is bigger and slower.",
                type_="int",
                default=150,
            ),
            Param(
                name="pages",
                description="Which pages to render, e.g. 1-3,7. Default: all.",
                type_="str",
            ),
        ),
    )
)

__all__ = ["SPEC"]
