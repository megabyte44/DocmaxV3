"""The local engine for ``split``.

The first tool to produce **many** outputs, and so the first real consumer of
``atomic_dir``. That matters more than the splitting does: the guarantee written
in Phase 2 — that a cancelled or failed multi-file run leaves no partial
directory — has until now been exercised only by its own unit tests.

Every part is staged in a temp directory beside the destination and swapped in
as a unit. A run interrupted after forty of a thousand pages leaves the
destination exactly as it was, rather than a directory holding the first forty.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _pagespec
from docmax.tools._pdf import open_pdf, page_count

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"

#: Zero-padded so a directory listing sorts the way the document reads. Four
#: digits covers a 9,999-page document; beyond that the width grows rather than
#: the ordering breaking.
_MIN_DIGITS = 4


class SplitLocal:
    """Cut a PDF into parts with pypdf."""

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
        """Split ``docs[0]`` into ``target``, one file per ``every`` pages."""
        import time

        from docmax.core.atomic import atomic_dir

        if not docs:
            raise InvalidParameterError(
                "Split needs a document.",
                remedy="Pass the PDF to split.",
            )

        every = _every(params)
        started = time.monotonic()

        document = docs[0]
        reader = open_pdf(document)
        total = page_count(reader)
        selected = _pagespec.parse(params.get("pages"), total=total)

        groups = [selected[index : index + every] for index in range(0, len(selected), every)]
        stem = document.path.stem
        width = max(_MIN_DIGITS, len(str(len(groups))))

        written: list[Path] = []
        progress.start(
            f"Splitting {len(selected)} page(s) into {len(groups)} file(s)", total=len(groups)
        )

        with atomic_dir(target, validators=(_produces(len(groups)),)) as staged:
            from pypdf import PdfWriter

            for number, group in enumerate(groups, start=1):
                # Between parts: nothing is at the destination yet, and the
                # staged directory is discarded wholesale by the writer.
                cancellation.raise_if_cancelled(operation="split")

                writer = PdfWriter()
                for index in group:
                    writer.add_page(reader.pages[index])

                part = staged / f"{stem}-{number:0{width}d}.pdf"
                with part.open("wb") as handle:
                    writer.write(handle)
                written.append(target.destination / part.name)
                progress.advance()

        return ToolResult(
            outputs=tuple(written),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "files": len(written),
                "pages": len(selected),
                "pages_per_file": every,
                "selection": _pagespec.describe(selected),
            },
        )


def _every(params: dict[str, Any]) -> int:
    """Pages per output file, validated before anything is opened."""
    value = params.get("every", 1)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParameterError(
            f"every must be a whole number, not {value!r}.",
            remedy="Use --every 1 for one file per page.",
            context={"parameter": "every"},
        )
    if value < 1:
        raise InvalidParameterError(
            f"every must be at least 1, not {value}.",
            remedy="Use --every 1 for one file per page.",
            context={"parameter": "every"},
        )
    return value


def _produces(expected: int) -> Any:
    """Validator: the staged directory holds exactly the parts we counted.

    Runs against the staged tree while the destination is still untouched, so a
    short split is never delivered.
    """

    def validate(produced: Path) -> None:
        from docmax.core.errors import OutputValidationError

        actual = len(list(produced.glob("*.pdf")))
        if actual != expected:
            raise OutputValidationError(
                f"Expected {expected} file(s) from the split, found {actual}.",
                context={"expected": expected, "actual": actual},
            )

    return validate


def _version() -> str:
    from importlib.metadata import version

    return f"{DEPENDENCY}/{version(DEPENDENCY)}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return SplitLocal()


__all__ = ["SplitLocal", "build"]
