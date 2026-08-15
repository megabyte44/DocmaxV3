"""The interfaces the layers meet at.

Everything here is a ``Protocol``: structural, so an implementation never imports
this module in order to *satisfy* one, and ``core`` never imports an
implementation in order to *use* one. That is the mechanism underneath the whole
layering. Inheritance would have inverted it — every tool importing the
foundation, and the foundation importing every tool to type its own registry.

:class:`ProgressSink` is why ``core`` can be forbidden from importing ``rich``.
Progress crosses the boundary as three method calls rather than as a
``rich.progress.Progress`` object, so the same engine reports into a terminal
bar, a Textual widget, a job row, or nothing at all, without knowing which.

**On required arguments.** ``run`` takes ``progress`` and ``cancellation`` as
required keyword arguments rather than optionals defaulting to ``None``. The
router supplies :class:`NullProgress` and ``NEVER_CANCELLED`` when a caller does
not care. This is deliberate: an optional would put ``if progress is not None``
at the top of every engine and around every checkpoint, and the branch that runs
in tests would then be the one users never hit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget, ToolResult


@runtime_checkable
class ProgressSink(Protocol):
    """Where an operation reports how far along it is.

    Implemented by a Rich progress bar in the CLI, a widget in the TUI, a log
    line or job row in the server, and by :class:`NullProgress` everywhere that
    does not care. Engines call it; they never construct one.

    Implementations must tolerate being called from a worker thread, and must
    not raise — a failure to *report* progress is never a reason to fail an
    operation that is otherwise succeeding.
    """

    def start(self, description: str, *, total: int | None = None) -> None:
        """Announce a unit of work. ``total=None`` means indeterminate."""

    def advance(self, amount: int = 1) -> None:
        """Report progress against the current total."""

    def finish(self) -> None:
        """The unit of work is over — successfully or otherwise.

        Called from a ``finally``, so it must be safe after a failure and safe
        to call when :meth:`start` was never reached.
        """


class NullProgress:
    """The do-nothing sink.

    Not an optimisation — a way to delete a branch. With this, no engine
    contains ``if progress is not None``, so there is one code path through
    every operation and it is the one that runs in tests.
    """

    __slots__ = ()

    def start(self, description: str, *, total: int | None = None) -> None:
        """Ignore."""

    def advance(self, amount: int = 1) -> None:
        """Ignore."""

    def finish(self) -> None:
        """Ignore."""


class Validator(Protocol):
    """A check run against the *staged* file, before it is swapped into place.

    Validators are what make "the operation produced a broken file" a condition
    the user never observes: the check runs while the destination is still
    untouched, and a failure discards the staged file instead of delivering it.
    v2 had no equivalent, and shipped ``extract_images`` output with ``.png``
    extensions and no PNG header.

    Raise :class:`docmax.core.errors.OutputValidationError` to fail. Anything
    else raised is treated as a bug in the validator and wrapped as one.
    """

    def __call__(self, produced: Path) -> None: ...


class EngineStrategy(Protocol):
    """One way of performing one operation.

    Each tool has up to two: a local strategy that does the work here, and a
    cloud strategy that posts it to an endpoint. They are interchangeable from
    the router's point of view, which is why neither may know the other exists.

    ``docs`` is a sequence because assembling tools — ``merge``,
    ``from-images`` — take several inputs. Single-input tools read ``docs[0]``.
    """

    def is_available(self) -> bool:
        """Can this strategy run *right now*, on this machine?

        Must be cheap and side-effect free: an ``importlib.util.find_spec`` or a
        ``shutil.which``, never an import of the heavy dependency itself and
        never a network call. The router asks this on every routing decision,
        including the ones that end up choosing the other engine — so importing
        OpenCV here would undo the lazy-loading guarantee entirely.
        """
        ...

    def unavailable_reason(self) -> str | None:
        """Why not, in one sentence, or ``None`` when available.

        The router quotes both engines' reasons in ``NoEngineAvailableError``,
        so "it didn't work" is never the whole message a user receives.
        """
        ...

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Perform the operation.

        Implementations must write through ``core.atomic`` rather than to
        ``target.destination`` directly, and must observe ``cancellation`` at
        points where stopping is safe.
        """
        ...


__all__ = ["EngineStrategy", "NullProgress", "ProgressSink", "Validator"]
