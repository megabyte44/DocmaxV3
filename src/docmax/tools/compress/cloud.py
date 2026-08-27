"""The cloud engine for ``compress``.

Two lines of tool-specific behaviour, and the rest is ``tools/_cloud.py``: which
tool name to send, and what to check about what comes back. Everything else --
upload, poll, fetch, atomic write -- is the shared flow, so a fix to it fixes
every cloud tool at once.

What is checked is the same thing the local engine checks, deliberately.
``compress`` must not change what the document *is*, and a smaller file with
fewer pages is not a compressed document -- it is the failure a user would be
least likely to notice, whichever machine produced it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.tools._cloud import CloudEngine
from docmax.tools._pdf import open_pdf, page_count
from docmax.tools.compress.validators import page_count_is

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from docmax.cloud_client import CloudClient
    from docmax.core.models import DocumentRef
    from docmax.core.protocols import EngineStrategy, Validator

TOOL_NAME = "compress"


def validators_for(
    docs: Sequence[DocumentRef],
    params: Mapping[str, Any],
) -> Sequence[Validator]:
    """Check the returned document has every page the source had.

    The source is opened *here*, before anything is uploaded, so a file that is
    not a readable PDF is refused without a round trip -- and so the expected
    count comes from the user's own copy rather than from anything the endpoint
    said about it.
    """
    return (page_count_is(page_count(open_pdf(docs[0]))),)


def build(client: CloudClient | None = None) -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return CloudEngine(TOOL_NAME, validators=validators_for, client=client)


__all__ = ["TOOL_NAME", "build", "validators_for"]
