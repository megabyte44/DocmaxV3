"""The cloud engine for ``to-images``.

Two lines of tool-specific behaviour, and the rest is ``tools/_cloud.py``: which
tool name to send, and what to check about what comes back -- plus the one flag
every directory-producing tool adds, ``produces_directory``, so the shared flow
knows this job's output is a zip archive rather than a single file. Everything
else -- upload, poll, fetch, unzip, atomic write -- is the shared flow, so a fix
to it fixes every cloud tool at once.

``ocr/cloud.py`` is the reference skeleton the M6 strategies were written
from; this is the reference for a *directory*-shaped one, and the file to copy
from for the next tool that joins cloud with the same shape.

## Why to-images is the other case cloud exists for

``ocr/cloud.py`` already makes the argument for Poppler: it is "the single most
painful install in the project". ``to-images`` depends on the exact same
binary -- see ``tools/_binaries.py``'s ``used_by=("ocr", "to-images")`` -- so a
user who can already run ``docmax ocr`` in the cloud without installing Poppler
has no reason to be blocked installing it just to render pages as images.
See [ADR 0034](../../../../docs/adr/0034-to-images-joins-the-cloud-engines.md).

## What is checked is what the local engine checks

The same validator, from the same module: the staged directory holds exactly
one real image per selected page, each one beginning with the byte signature
its format requires. A cloud render that came back with a page missing, or a
file that is not really a PNG, fails exactly where a local one would, and the
destination is untouched either way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.tools import _pagespec
from docmax.tools._cloud import CloudEngine
from docmax.tools._pdf import open_pdf, page_count
from docmax.tools.to_images.validators import image_format_for, renders_images

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from docmax.cloud_client import CloudClient
    from docmax.core.models import DocumentRef
    from docmax.core.protocols import EngineStrategy, Validator

TOOL_NAME = "to-images"


def validators_for(
    docs: Sequence[DocumentRef],
    params: Mapping[str, Any],
) -> Sequence[Validator]:
    """Check the returned directory holds one real image per selected page.

    The source is opened, and the page selection and format resolved, *here* --
    before anything is uploaded -- so a bad ``--pages``, a nonsense ``--format``,
    or an unreadable PDF is refused without a round trip, and the expected count
    comes from the user's own copy rather than from anything the endpoint said
    about it.
    """
    total = page_count(open_pdf(docs[0]))
    selected = _pagespec.parse(params.get("pages"), total=total)
    image_format = image_format_for(params)
    return (renders_images(len(selected), image_format),)


def build(client: CloudClient | None = None) -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return CloudEngine(TOOL_NAME, validators=validators_for, client=client, produces_directory=True)


__all__ = ["TOOL_NAME", "build", "validators_for"]
