"""Metadata for ``ocr``.

Declaring both engines here is what lets the router offer the cloud fallback
*by name* when Tesseract is missing, and what lets ``GET /v1/capabilities`` list
this tool without the server keeping a second list of its own.
"""

from __future__ import annotations

from docmax.core.models import Engine
from docmax.core.registry import Param, ToolSpec, register

SPEC = register(
    ToolSpec(
        name="ocr",
        summary="Add a searchable text layer to a scanned document.",
        category="extract",
        module=__name__.rpartition(".")[0],
        supported_engines=frozenset({Engine.LOCAL, Engine.CLOUD}),
        default_suffix=".pdf",
        params=(
            Param(
                name="lang",
                description="Language code passed to the OCR engine, e.g. eng or deu.",
                type_="str",
                default="eng",
            ),
            Param(
                name="dpi",
                description="Rasterisation resolution. Higher is slower and usually better.",
                type_="int",
                default=300,
            ),
            Param(
                name="deskew",
                description="Straighten pages before recognition.",
                type_="bool",
                default=True,
            ),
        ),
    )
)

__all__ = ["SPEC"]
