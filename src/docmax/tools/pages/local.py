"""The local engine for ``pages``.

``select`` and ``delete`` are two ways of saying the same thing and are mutually
exclusive — accepting both would mean inventing a precedence nobody asked for.

pypdf is imported inside the methods, not at module scope.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _pagespec
from docmax.tools._pdf import open_pdf, page_count, save
from docmax.tools.pages.validators import page_count_is

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"


class PagesLocal:
    """Keep or drop pages with pypdf."""

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
        """Keep or drop pages of ``docs[0]``, writing the result to ``target``.

        Repeating a page in ``select`` is allowed and produces it twice: unlike
        ``reorder``, extracting the same page more than once is a request people
        genuinely make.
        """
        import time

        from pypdf import PdfWriter

        if not docs:
            raise InvalidParameterError(
                "Pages needs a document.",
                remedy="Pass the PDF to work on.",
            )

        select = params.get("select")
        delete = params.get("delete")
        if select and delete:
            raise InvalidParameterError(
                "Choose either the pages to keep or the pages to delete, not both.",
                remedy="Use --select 1-3 or --delete 4, not both at once.",
                context={"parameter": "select"},
            )
        if not select and not delete:
            raise InvalidParameterError(
                "Say which pages to keep or delete.",
                remedy="Use --select 1-3 to keep, or --delete 4 to remove.",
                context={"parameter": "select"},
            )

        started = time.monotonic()
        reader = open_pdf(docs[0])
        total = page_count(reader)

        if select:
            kept = _pagespec.parse(select, total=total)
        else:
            removed = set(_pagespec.parse(delete, total=total))
            kept = tuple(index for index in range(total) if index not in removed)
            if not kept:
                raise InvalidParameterError(
                    "Deleting those pages would leave an empty document.",
                    remedy="Keep at least one page.",
                    context={"parameter": "delete"},
                )

        writer = PdfWriter()
        progress.start(f"Keeping {len(kept)} of {total} page(s)", total=len(kept))
        for index in kept:
            cancellation.raise_if_cancelled(operation="pages")
            writer.add_page(reader.pages[index])
            progress.advance()

        save(writer, target, validators=(page_count_is(len(kept)),))

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "pages": len(kept),
                "removed": total - len(kept),
                "selection": _pagespec.describe(kept),
            },
        )


def _version() -> str:
    from importlib.metadata import version

    return f"{DEPENDENCY}/{version(DEPENDENCY)}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return PagesLocal()


__all__ = ["PagesLocal", "build"]
