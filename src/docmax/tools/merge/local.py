"""The local engine for ``merge``.

Nothing in here is imported until the router has resolved ``local`` for a
``merge`` call — which is why the pypdf import sits inside the methods that use
it rather than at module scope. ``tests/hygiene/test_no_heavy_imports.py`` runs
that check in a subprocess.

The strategy declares no base class. It satisfies ``EngineStrategy`` structurally,
and :func:`build`'s return annotation is what makes mypy verify that it does.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.errors import (
    CorruptDocumentError,
    EncryptedDocumentError,
    InvalidParameterError,
    UnsupportedFormatError,
)
from docmax.core.models import Engine, ToolResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pypdf import PdfReader

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"


class MergeLocal:
    """Concatenate PDFs with pypdf."""

    def is_available(self) -> bool:
        # find_spec, not an import: availability is asked on every routing
        # decision, including the ones that end up choosing the other engine.
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
        """Merge ``docs`` into ``target``, in the order given.

        Pages are appended into a temp file on the destination's filesystem,
        the validators check it, and only then is it renamed into place — so a
        failure anywhere below leaves the destination exactly as it was.

        **Progress convention, worth copying.** The tool calls ``start`` and
        ``advance``; it does *not* call ``finish``. Only the tool knows what the
        work is called and how many units it has, and only the router can
        guarantee the region closes on every path — it does so in a ``finally``.
        A tool that finished its own sink would double-finish on the happy path
        and still leak on the paths it did not anticipate.
        """
        import time

        from docmax.core.atomic import atomic_write
        from docmax.tools.merge.validators import is_readable_pdf, page_count_is

        if not docs:
            raise InvalidParameterError(
                "Merge needs at least one document.",
                remedy="Pass the files to merge, in the order you want them.",
            )

        outline = self._outline_option(params)
        started = time.monotonic()

        # pypdf is imported here rather than at module scope so that discovering
        # this tool — which happens on every `--help` — costs nothing.
        from pypdf import PdfWriter

        writer = PdfWriter()
        bookmarks: list[tuple[str, int]] = []

        progress.start(f"Merging {len(docs)} document(s)", total=len(docs))
        for document in docs:
            # Between files is the safe checkpoint: nothing is on disk yet, and
            # the staged file is discarded by the writer below.
            cancellation.raise_if_cancelled(operation="merge")

            reader = self._open(document)
            bookmarks.append((document.path.stem, len(writer.pages)))
            for page in reader.pages:
                writer.add_page(page)
            progress.advance()

        if outline:
            for title, first_page in bookmarks:
                writer.add_outline_item(title, first_page)

        pages = len(writer.pages)
        cancellation.raise_if_cancelled(operation="merge")

        with atomic_write(
            target,
            validators=(is_readable_pdf, page_count_is(pages)),
        ) as handle:
            writer.write(handle)

        return self._result(
            target,
            duration_ms=int((time.monotonic() - started) * 1000),
            pages=pages,
        )

    @staticmethod
    def _outline_option(params: dict[str, Any]) -> bool:
        """Read the ``outline`` parameter, or explain what it should have been.

        Declared in ``tool.py`` as a bool defaulting to true. Both interfaces
        validate parameters against that declaration before calling, so this is
        a second line rather than the first — but a library caller reaches this
        method directly, and ``outline="yes"`` silently meaning *true* is the
        class of bug the project's config layer already refuses.
        """
        value = params.get("outline", True)
        if isinstance(value, bool):
            return value
        raise InvalidParameterError(
            f"outline must be true or false, not {value!r}.",
            remedy="Pass --outline or --no-outline.",
            context={"parameter": "outline"},
        )

    @staticmethod
    def _open(document: DocumentRef) -> PdfReader:
        """Open one input, or raise the typed error that names what is wrong.

        Three distinct failures, three distinct errors, because "merge failed"
        sends a user looking in the wrong place: a Word document, a damaged
        file, and a password-protected one each need a different next step.
        """
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        if document.suffix != ".pdf":
            raise UnsupportedFormatError(
                f"merge only reads PDFs, and {document.path.name} is not one.",
                context={"path": str(document.path), "suffix": document.suffix},
            )

        try:
            reader = PdfReader(str(document.path))
        except (PyPdfError, OSError, ValueError) as exc:
            raise CorruptDocumentError(
                f"{document.path.name} could not be read as a PDF: {exc}",
                context={"path": str(document.path)},
            ) from exc

        if reader.is_encrypted:
            # Checked before touching .pages, which raises its own error for
            # this case with a far less useful message.
            raise EncryptedDocumentError(
                f"{document.path.name} is password-protected.",
                context={"path": str(document.path)},
            )

        return reader

    def _result(self, target: OutputTarget, *, duration_ms: int, pages: int) -> ToolResult:
        """Shape of what ``run`` returns, once it does.

        ``engine_version`` names whatever actually did the work, in the same
        form the cloud engine reports it (``gs/10.03.0``), so a result is
        traceable to an implementation regardless of which engine produced it.
        """
        from importlib.metadata import version

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=duration_ms,
            engine_version=f"{DEPENDENCY}/{version(DEPENDENCY)}",
            details={"pages": pages},
        )


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return MergeLocal()


__all__ = ["MergeLocal", "build"]
