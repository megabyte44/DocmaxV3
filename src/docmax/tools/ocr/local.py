"""The local engine for ``ocr``.

Four separate things have to be present for this to run: the Python bindings,
the Tesseract binary, a language pack, and Poppler for rasterisation. Any of
them can be missing on its own, so availability reports *which* — a router that
can only say "unavailable" cannot tell the user what to install, and cannot
explain why it is offering the cloud engine instead.

Every import of the heavy half sits inside a method. The module itself costs
nothing to import.
"""

from __future__ import annotations

import importlib.util
import shutil
from typing import TYPE_CHECKING, Any

from docmax.core.branding import CLI_NAME
from docmax.core.models import Engine, ToolResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

#: Installed by the ``ocr`` extra. ``cv2`` is the import name of opencv.
PYTHON_DEPENDENCIES = ("pytesseract", "pdf2image", "cv2")

#: External programs, installed by ``setup`` (M3) and reported by ``doctor``.
BINARIES = ("tesseract", "pdftoppm")

INSTALL_HINT = f"Run `{CLI_NAME} setup --ocr`, or use --engine cloud to skip the install."


def missing_dependencies() -> tuple[str, ...]:
    """Everything this engine needs and cannot find, in one list.

    Kept module-level and public so the router can quote it when it builds the
    ``LocalDependencyMissingError`` that justifies the cloud fallback.
    """
    missing = [name for name in PYTHON_DEPENDENCIES if importlib.util.find_spec(name) is None]
    missing.extend(name for name in BINARIES if shutil.which(name) is None)
    return tuple(missing)


class OcrLocal:
    """Rasterise, preprocess, recognise, and rebuild with a text layer."""

    def is_available(self) -> bool:
        return not missing_dependencies()

    def unavailable_reason(self) -> str | None:
        missing = missing_dependencies()
        if not missing:
            return None
        return f"Not installed: {', '.join(missing)}. {INSTALL_HINT}"

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """OCR ``docs[0]`` into ``target``. Lands in M8.

        Two rules this implementation is bound by, both from failures v2
        shipped: every subprocess call carries a timeout, and every temp
        artefact is cleaned up. v2 wrote a ``_preprocessed.png`` beside each
        source file, which folder-watch mode then treated as new input.
        """
        raise NotImplementedError

    def _result(self, target: OutputTarget, *, duration_ms: int, pages: int) -> ToolResult:
        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=duration_ms,
            engine_version=self._engine_version(),
            details={"pages": pages},
        )

    def _engine_version(self) -> str:
        import pytesseract

        return f"{BINARIES[0]}/{pytesseract.get_tesseract_version()}"


def build() -> EngineStrategy:
    return OcrLocal()


__all__ = ["OcrLocal", "build", "missing_dependencies"]
