"""The local engine for ``metadata``.

Two modes in one tool, because they are two halves of one job: reading the
fields, and setting them. With no ``set`` it reads and writes nothing at all —
the answer travels in the ``ToolResult``, and ``outputs`` is empty.

pypdf is imported inside the methods, not at module scope.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools._pdf import metadata_of, open_pdf, page_count, save
from docmax.tools.metadata.validators import page_count_is

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"

#: The standard document information fields. Restricted deliberately: a typo
#: like ``Titel=x`` would otherwise write a field no reader ever shows, and the
#: user would believe they had set the title. Same reasoning as the config
#: layer's refusal of unknown keys.
WRITABLE = ("Title", "Author", "Subject", "Keywords", "Creator", "Producer")


class MetadataLocal:
    """Read or set the document information fields with pypdf."""

    def is_available(self) -> bool:
        return importlib.util.find_spec(DEPENDENCY) is not None

    def unavailable_reason(self) -> str | None:
        if self.is_available():
            return None
        return f"{DEPENDENCY} is not installed."

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Read the metadata, or write it and produce a new document.

        ``target`` is **unused when reading**. The tool contract requires one
        and there is nothing to write; see the note in ``get_info/local.py``,
        which has the same shape and where the friction is described.
        """
        import time

        if not docs:
            raise InvalidParameterError(
                "Metadata needs a document.",
                remedy="Pass the PDF to inspect.",
            )

        assignments = _assignments(params.get("set"))
        clear = _clear_flag(params)
        started = time.monotonic()

        reader = open_pdf(docs[0])
        existing = metadata_of(reader)

        if not assignments and not clear:
            # Read-only: nothing is written and nothing is staged.
            return ToolResult(
                outputs=(),
                engine_used=Engine.LOCAL,
                duration_ms=int((time.monotonic() - started) * 1000),
                engine_version=_version(),
                details={"metadata": existing, "fields": len(existing)},
            )

        from pypdf import PdfWriter

        total = page_count(reader)
        writer = PdfWriter()
        progress.start(f"Rewriting {total} page(s)", total=total)
        for index in range(total):
            cancellation.raise_if_cancelled(operation="metadata")
            writer.add_page(reader.pages[index])
            progress.advance()

        # `clear` drops what was there; otherwise the untouched fields are
        # carried across, so setting a title does not silently erase an author.
        final = {} if clear else dict(existing)
        final.update(assignments)
        writer.add_metadata(final)

        save(writer, target, validators=(page_count_is(total),))

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "metadata": {str(k): str(v) for k, v in final.items()},
                "fields": len(final),
                "written": sorted(assignments),
                "cleared": clear,
            },
        )


def _clear_flag(params: dict[str, Any]) -> bool:
    value = params.get("clear", False)
    if isinstance(value, bool):
        return value
    raise InvalidParameterError(
        f"clear must be true or false, not {value!r}.",
        remedy="Pass --clear or leave it out.",
        context={"parameter": "clear"},
    )


def _assignments(raw: Any) -> dict[str, str]:
    """Parse ``Title=Report`` pairs into the PDF's ``/Title`` form.

    Accepts one string or several, so a CLI can pass ``--set`` more than once
    without inventing an escaping rule for commas inside a value.
    """
    if raw is None:
        return {}

    items: list[str] = [raw] if isinstance(raw, str) else list(raw)
    known = {name.lower(): name for name in WRITABLE}
    parsed: dict[str, str] = {}

    for item in items:
        if not isinstance(item, str) or "=" not in item:
            raise InvalidParameterError(
                f"{item!r} is not a Field=Value pair.",
                remedy='Write it as --set Title="My Report".',
                context={"parameter": "set"},
            )
        field, _, value = item.partition("=")
        name = known.get(field.strip().lstrip("/").lower())
        if name is None:
            raise InvalidParameterError(
                f"{field.strip()!r} is not a document metadata field.",
                remedy=f"Use one of: {', '.join(WRITABLE)}.",
                context={"parameter": "set", "field": field.strip()},
            )
        parsed[f"/{name}"] = value

    return parsed


def _version() -> str:
    from importlib.metadata import version

    return f"{DEPENDENCY}/{version(DEPENDENCY)}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return MetadataLocal()


__all__ = ["WRITABLE", "MetadataLocal", "build"]
