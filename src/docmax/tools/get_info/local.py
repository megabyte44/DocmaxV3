"""The local engine for ``get-info``.

Read-only: it never writes anything, and ``outputs`` is always empty. The answer
travels in ``ToolResult.details``, which is where structured data belongs — an
interface then renders it as a table, JSON, or an MCP response without this tool
knowing which.

## A note on ``target``

``EngineStrategy.run`` requires an ``OutputTarget``, and this tool has no output.
The argument is accepted and ignored.

That is a genuine seam in the contract rather than a tidy design: `ToolSpec` has
no way to say "this tool produces nothing", so the router resolves a destination
that is never used, and an interface has to avoid handing it a path that would
trip `OutputExistsError` for a file it was never going to write. The CLI
therefore builds the target directly for read-only commands instead of calling
`router.target_for`.

The clean fix is a `produces_output` flag on `ToolSpec` that the router honours,
which is a change to core and to the registry. It is **not** made here; it is
reported for a decision, because changing a contract to suit two tools is the
kind of thing that should be decided deliberately rather than discovered in a
diff.

pypdf is imported inside the method, not at module scope.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools._pdf import metadata_of, open_pdf, page_count, require_pdf

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"


class GetInfoLocal:
    """Report what a document is, without changing it."""

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
        """Describe ``docs[0]``. Writes nothing; ``target`` is unused."""
        import time

        if not docs:
            raise InvalidParameterError(
                "Get-info needs a document.",
                remedy="Pass the PDF to inspect.",
            )

        started = time.monotonic()
        document = docs[0]
        require_pdf(document)

        # Encryption is reported rather than refused: "is this file locked?" is
        # exactly the question someone runs this tool to answer, so failing on
        # an encrypted file would refuse to answer the question it was asked.
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        try:
            reader = PdfReader(str(document.path))
            encrypted = bool(reader.is_encrypted)
        except (PyPdfError, OSError, ValueError) as exc:
            from docmax.core.errors import CorruptDocumentError

            raise CorruptDocumentError(
                f"{document.path.name} could not be read as a PDF: {exc}",
                context={"path": str(document.path)},
            ) from exc

        details: dict[str, Any] = {
            "path": str(document.path),
            "name": document.path.name,
            "size_bytes": document.size_bytes,
            "encrypted": encrypted,
        }

        if encrypted:
            # The page tree is unreadable without the password. Report what is
            # knowable rather than guessing or failing.
            details["pages"] = None
            details["metadata"] = {}
        else:
            reader = open_pdf(document)
            details["pages"] = page_count(reader)
            details["metadata"] = metadata_of(reader)

        return ToolResult(
            outputs=(),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details=details,
        )


def _version() -> str:
    from importlib.metadata import version

    return f"{DEPENDENCY}/{version(DEPENDENCY)}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return GetInfoLocal()


__all__ = ["GetInfoLocal", "build"]
