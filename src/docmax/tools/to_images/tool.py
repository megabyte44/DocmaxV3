"""Metadata for ``to-images``.

Imported during discovery -- on every ``--help`` -- so it imports nothing but
``core`` and the format table, and does no work at import time. Poppler is not
looked for here; that happens in ``local.py``.
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
        # No cloud engine. Poppler is a small, packaged install on every
        # platform -- nothing like the pain Tesseract or a LaTeX distribution
        # is -- so uploading a document to rasterise it would buy nothing.
        supported_engines=frozenset({Engine.LOCAL}),
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
