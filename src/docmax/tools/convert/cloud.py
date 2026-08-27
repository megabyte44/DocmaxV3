"""The cloud engine for ``convert``.

The tool-specific half is the same two things ``compress``'s is: the tool name,
and what to check about the result. Everything else is ``tools/_cloud.py``.

The format rules are *not* re-implemented here. ``--to pdf`` and a PDF input are
refused before a strategy is chosen, by the same ``_formats`` table the local
engine validates against -- so the cloud path cannot quietly accept a conversion
the local path refuses. Whether Pandoc is installed *here* is irrelevant to the
cloud engine; whether the format is one DocMax supports is not, and that is a
question about the request rather than about the machine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.tools import _formats
from docmax.tools._cloud import CloudEngine
from docmax.tools.convert.validators import writes_readable_output

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from docmax.cloud_client import CloudClient
    from docmax.core.models import DocumentRef
    from docmax.core.protocols import EngineStrategy, Validator

TOOL_NAME = "convert"


def validators_for(
    docs: Sequence[DocumentRef],
    params: Mapping[str, Any],
) -> Sequence[Validator]:
    """Check the returned file is the format that was asked for.

    Resolving ``--to`` here also validates it, which is why a nonsense target
    format is refused before an upload rather than by the endpoint afterwards.
    The input's own format is checked for the same reason.
    """
    target = _formats.document(str(params.get("to", "")))
    if not target.writable:
        raise _formats.reject_unwritable_document(target)

    source = _formats.document_for_suffix(docs[0].suffix)
    if source is None or not source.readable:
        raise _formats.reject_unreadable_document(docs[0].suffix, name=docs[0].path.name)

    return (writes_readable_output(target),)


def build(client: CloudClient | None = None) -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return CloudEngine(TOOL_NAME, validators=validators_for, client=client)


__all__ = ["TOOL_NAME", "build", "validators_for"]
