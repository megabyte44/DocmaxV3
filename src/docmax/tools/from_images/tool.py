"""Metadata for ``from-images``.

Imported during discovery -- on every ``--help`` -- so it imports nothing but
``core`` and the format table, and does no work at import time. Pillow and
img2pdf live in ``local.py``, which nobody touches until this tool runs.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import ToolSpec, register

SPEC = register(
    ToolSpec(
        name="from-images",
        summary="Combine images into a PDF, one page per image.",
        category="convert",
        module=__name__.rpartition(".")[0],
        # Pure Python. Uploading a folder of images in order to staple them
        # together would be slower, less private, and would need a network.
        supported_engines=frozenset({Engine.LOCAL}),
        # The second multi-input tool, after `merge`. `core/protocols.py` and
        # `core/registry.py` have both named it as the example since M1.
        accepts_multiple_inputs=True,
        default_suffix=".pdf",
        # No parameters. Order is argument order, and page size follows each
        # image's own dimensions -- see `local.py` for why neither is a flag.
        params=(),
    )
)

__all__ = ["SPEC"]
