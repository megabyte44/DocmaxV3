"""The interfaces the layers meet at.

Everything here is a ``Protocol``: structural, so an implementation never
imports this module to satisfy it, and ``core`` never imports an implementation
to use it. That is the whole trick behind the layering — the same core drives a
CLI, a TUI, a batch runner, the M10 MCP server, and the self-hosted API server
without modification.

``ProgressSink`` in particular is why ``core`` can be forbidden from importing
``rich``: progress crosses the boundary as three method calls rather than as a
``rich.progress.Progress`` object.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from docmax.core.models import DocumentRef, OutputTarget, ToolResult


@runtime_checkable
class ProgressSink(Protocol):
    """Where an operation reports how far along it is.

    Implemented by a rich progress bar in the CLI, a widget in the TUI, a log
    line in the server, and by :class:`NullProgress` everywhere that does not
    care. Engines call it; they never construct one.
    """

    def start(self, description: str, *, total: int | None = None) -> None:
        """Announce a unit of work. ``total=None`` means indeterminate."""

    def advance(self, amount: int = 1) -> None:
        """Report progress against the current total."""

    def finish(self) -> None:
        """The unit of work is over, successfully or otherwise."""


class NullProgress:
    """The do-nothing sink, so no engine needs ``if progress is not None``."""

    def start(self, description: str, *, total: int | None = None) -> None:
        """Ignore."""

    def advance(self, amount: int = 1) -> None:
        """Ignore."""

    def finish(self) -> None:
        """Ignore."""


class Validator(Protocol):
    """A check run against the *temp* file, before it is swapped into place.

    Validators are what make "the operation produced a broken file" a condition
    the user never observes: the check runs while the destination is still
    untouched, and a failure discards the temp file instead of delivering it.
    Raise :class:`docmax.core.errors.OutputValidationError` to fail.
    """

    def __call__(self, produced: Path) -> None: ...


class EngineStrategy(Protocol):
    """One way of performing one operation.

    Each tool has up to two: a local strategy that does the work here, and a
    cloud strategy that posts it to an endpoint. They are interchangeable from
    the router's point of view, which is why neither one may know about the
    other.

    ``docs`` is a sequence because assembling tools (``merge``, ``from-images``)
    take several inputs; single-input tools read ``docs[0]``.
    """

    def is_available(self) -> bool:
        """Can this strategy run *right now*, on this machine?

        Cheap and side-effect free: a ``find_spec`` or a ``shutil.which``, never
        an import of the heavy dependency itself and never a network call.
        """
        ...

    def unavailable_reason(self) -> str | None:
        """Why not, in one sentence, or ``None`` when available.

        The router quotes both engines' reasons in ``NoEngineAvailableError``,
        so "it didn't work" is never the whole message a user gets.
        """
        ...

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink | None = None,
        **params: Any,
    ) -> ToolResult: ...


__all__ = ["EngineStrategy", "NullProgress", "ProgressSink", "Validator"]
