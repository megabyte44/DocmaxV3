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

from docmax.core.models import Engine, ToolResult

if TYPE_CHECKING:
    from collections.abc import Sequence

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
        """Merge ``docs`` into ``target``.

        Lands in M1 together with ``core/atomic.py``: pages are appended into a
        temp file on the destination's filesystem, ``validators.page_count_is``
        checks it, and only then is it renamed into place.
        """
        raise NotImplementedError

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
