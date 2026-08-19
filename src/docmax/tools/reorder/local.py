"""The local engine for ``reorder``.

Stricter than ``pages``: the order must be a complete permutation, because a
reorder that quietly drops a page is a loss nobody notices until much later.

pypdf is imported inside the methods, not at module scope.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _pagespec
from docmax.tools._pdf import open_pdf, page_count, save
from docmax.tools.reorder.validators import page_count_is

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"


class ReorderLocal:
    """Rearrange pages with pypdf."""

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
        """Write ``docs[0]``'s pages to ``target`` in the requested order."""
        import time

        from pypdf import PdfWriter

        if not docs:
            raise InvalidParameterError(
                "Reorder needs a document.",
                remedy="Pass the PDF to reorder.",
            )

        order = params.get("order")
        if not order:
            raise InvalidParameterError(
                "Reorder needs the new page order.",
                remedy="Pass --order 3,1,2.",
                context={"parameter": "order"},
            )

        started = time.monotonic()
        reader = open_pdf(docs[0])
        total = page_count(reader)
        sequence = _pagespec.parse(order, total=total)
        _require_permutation(sequence, total=total)

        writer = PdfWriter()
        progress.start(f"Reordering {total} page(s)", total=total)
        for index in sequence:
            cancellation.raise_if_cancelled(operation="reorder")
            writer.add_page(reader.pages[index])
            progress.advance()

        save(writer, target, validators=(page_count_is(total),))

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={"pages": total, "order": _pagespec.describe(sequence)},
        )


def _require_permutation(sequence: tuple[int, ...], *, total: int) -> None:
    """Every page exactly once — none repeated, none missing.

    Deliberately stricter than ``pages``. Repeating a page there is a legitimate
    request; here it means the user miscounted, and a reorder that silently
    dropped or duplicated a page would be discovered far too late.
    """
    if sorted(sequence) == list(range(total)):
        return

    repeated = sorted({index + 1 for index in sequence if sequence.count(index) > 1})
    missing = sorted(set(range(1, total + 1)) - {index + 1 for index in sequence})

    problems = []
    if repeated:
        problems.append(f"repeated: {', '.join(map(str, repeated))}")
    if missing:
        problems.append(f"missing: {', '.join(map(str, missing))}")

    raise InvalidParameterError(
        f"The order must list all {total} page(s) exactly once ({'; '.join(problems)}).",
        remedy=f"List every page from 1 to {total}, in the order you want them.",
        context={"parameter": "order", "repeated": repeated, "missing": missing},
    )


def _version() -> str:
    from importlib.metadata import version

    return f"{DEPENDENCY}/{version(DEPENDENCY)}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return ReorderLocal()


__all__ = ["ReorderLocal", "build"]
