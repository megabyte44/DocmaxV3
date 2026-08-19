"""The local engine for ``sanitize``.

What it removes is listed exactly in :meth:`SanitizeLocal.run`, and the list is
short. Overclaiming here would be worse than claiming nothing: someone who
believes a file is safe because DocMax said so is worse off than someone who
knows it was only partly checked.

pypdf is imported inside the methods, not at module scope.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools._pdf import open_pdf, page_count, save
from docmax.tools.sanitize.validators import page_count_is

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"

#: Page-level entries carrying executable or navigable actions. These survive a
#: page-by-page rebuild — verified against pypdf, not assumed — so they are the
#: two that have to be deleted by hand.
_PAGE_ENTRIES = ("/AA", "/Annots")


class SanitizeLocal:
    """Rebuild a document without its active content."""

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
        """Rebuild ``docs[0]`` into ``target`` without its active content.

        **Exactly what this removes.** Verified against pypdf's behaviour rather
        than assumed:

        * document-level **JavaScript** and **embedded files**, which live under
          the catalog's ``/Names`` tree;
        * ``/OpenAction`` — whatever the document asks to happen when it opens;
        * ``/AcroForm`` — interactive form fields and their actions;
        * page-level ``/AA`` additional actions, which fire on page open/close;
        * page ``/Annots`` — annotations, which carry link, launch and
          JavaScript actions.

        The mechanism is a page-by-page rebuild. A fresh document is assembled
        from page content alone, so catalog-level structures are never carried
        across rather than being hunted down and deleted — which means a
        carrier this code has never heard of is dropped too. ``/AA`` and
        ``/Annots`` live *on the page* and do survive that rebuild, so they are
        removed explicitly.

        **What this does not do.** It does not inspect content streams, so it
        will not find anything hidden in the page drawing operators, and it is
        no defence against a PDF crafted to attack your viewer's parser. It
        removes the standard carriers of active content. Nothing more.

        The cost, worth stating: outlines, form data and link annotations are
        lost along with the actions, because this cannot tell a benign link
        from a malicious one and will not guess.
        """
        import time

        from pypdf import PdfWriter
        from pypdf.generic import NameObject

        if not docs:
            raise InvalidParameterError(
                "Sanitize needs a document.",
                remedy="Pass the PDF to clean.",
            )

        started = time.monotonic()
        reader = open_pdf(docs[0])
        total = page_count(reader)

        writer = PdfWriter()
        removed = 0
        progress.start(f"Sanitising {total} page(s)", total=total)
        for index in range(total):
            cancellation.raise_if_cancelled(operation="sanitize")
            page = reader.pages[index]
            for entry in _PAGE_ENTRIES:
                key = NameObject(entry)
                if key in page:
                    del page[key]
                    removed += 1
            writer.add_page(page)
            progress.advance()

        save(writer, target, validators=(page_count_is(total),))

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={"pages": total, "page_entries_removed": removed},
        )


def _version() -> str:
    from importlib.metadata import version

    return f"{DEPENDENCY}/{version(DEPENDENCY)}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return SanitizeLocal()


__all__ = ["SanitizeLocal", "build"]
