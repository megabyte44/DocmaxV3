"""The cloud engine for ``ocr``.

Two lines of tool-specific behaviour, and the rest is ``tools/_cloud.py``: which
tool name to send, and what to check about what comes back. Everything else --
upload, poll, fetch, atomic write -- is the shared flow, so a fix to it fixes
every cloud tool at once.

This module was the *reference skeleton* the M6 strategies were written from,
and it kept a hand-rolled ``OcrCloud`` class through five milestones because
[ADR 0012](../../../docs/adr/0012-cloud-engines-are-compress-and-convert.md)
deliberately held OCR back to M8. The class is gone now: keeping it would have
meant a second implementation of the flow ``_cloud.py`` owns, in the one tool
most likely to need the flow's fixes.

## Why OCR is the case cloud exists for

Ghostscript is one package. OCR is Tesseract, a language pack per language, and
Poppler -- the single most painful install in the project, and the one
``architecture/overview.md`` has used to justify the Cloud Engine since M0. The
endpoint is a machine that already has all of it.

## What is checked is what the local engine checks

The same two validators, from the same module: no page added or dropped, and
the result actually carries extractable text. A cloud OCR that came back with a
blank text layer -- a wrong language pack on the server, a bad rasterisation --
fails exactly where a local one would, and the destination is untouched either
way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.tools._cloud import CloudEngine
from docmax.tools._pdf import open_pdf, page_count
from docmax.tools.ocr.validators import checks_for

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from docmax.cloud_client import CloudClient
    from docmax.core.models import DocumentRef
    from docmax.core.protocols import EngineStrategy, Validator

TOOL_NAME = "ocr"


def validators_for(
    docs: Sequence[DocumentRef],
    params: Mapping[str, Any],
) -> Sequence[Validator]:
    """Check the returned document page for page, and for a real text layer.

    The source is opened *here*, before anything is uploaded, so a file that is
    not a readable PDF is refused without a round trip -- and so the expected
    count comes from the user's own copy rather than from anything the endpoint
    said about it.
    """
    return checks_for(page_count(open_pdf(docs[0])))


def build(client: CloudClient | None = None) -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return CloudEngine(TOOL_NAME, validators=validators_for, client=client)


__all__ = ["TOOL_NAME", "build", "validators_for"]
