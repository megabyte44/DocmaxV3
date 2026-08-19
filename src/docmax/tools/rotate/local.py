"""The local engine for ``rotate``.

Rotation is stored as a quarter turn in the page dictionary, so this rewrites
metadata rather than re-rendering anything.

pypdf is imported inside the methods, not at module scope: discovery happens on
every ``--help``, and it must not cost what running costs.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _pagespec
from docmax.tools._pdf import open_pdf, page_count, save
from docmax.tools.rotate.validators import page_count_is

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"


class RotateLocal:
    """Turn pages a quarter at a time with pypdf."""

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
        """Rotate the selected pages of ``docs[0]`` into ``target``."""
        import time

        from pypdf import PdfWriter

        if not docs:
            raise InvalidParameterError(
                "Rotate needs a document.",
                remedy="Pass the PDF to rotate.",
            )

        degrees = _degrees(params)
        started = time.monotonic()

        reader = open_pdf(docs[0])
        total = page_count(reader)
        selected = set(_pagespec.parse(params.get("pages"), total=total))

        writer = PdfWriter()
        progress.start(f"Rotating {len(selected)} of {total} page(s)", total=total)
        for index in range(total):
            cancellation.raise_if_cancelled(operation="rotate")
            page = reader.pages[index]
            if index in selected:
                # Rotation accumulates on a page that already carried one, which
                # is what "rotate this page" means when it is already sideways.
                page.rotate(degrees)
            writer.add_page(page)
            progress.advance()

        save(writer, target, validators=(page_count_is(total),))

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={"pages": total, "rotated": len(selected), "degrees": degrees},
        )


def _degrees(params: dict[str, Any]) -> int:
    """A rotation pypdf can apply, or an error naming what is allowed.

    Multiples of 90 only. A PDF stores rotation as one of four quarter turns, so
    45 degrees is not a finer version of a supported request — it is not
    representable at all, and silently rounding it would be worse than refusing.
    """
    value = params.get("by", 90)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParameterError(
            f"by must be a whole number of degrees, not {value!r}.",
            remedy="Use 90, 180 or 270.",
            context={"parameter": "by"},
        )
    if value % 90 != 0:
        raise InvalidParameterError(
            f"A PDF can only be rotated in quarter turns, not by {value} degrees.",
            remedy="Use 90, 180 or 270.",
            context={"parameter": "by"},
        )
    # Normalised to 0-359. pypdf stores whatever it is handed, so without this
    # `--by 360` writes /Rotate 360 and `--by -90` writes -90 — both legal
    # arithmetic and both odd to find in a file. 360 becomes a no-op, and -90
    # becomes the 270 the user meant.
    return value % 360


def _version() -> str:
    from importlib.metadata import version

    return f"{DEPENDENCY}/{version(DEPENDENCY)}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return RotateLocal()


__all__ = ["RotateLocal", "build"]
