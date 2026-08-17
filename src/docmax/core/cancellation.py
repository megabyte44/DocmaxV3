"""Stopping work that is already running, without knowing who asked.

Cancellation arrives from somewhere different in every interface: Ctrl-C in the
CLI, a key press in the TUI, a dropped connection in the server, a parent job
abandoning its children in a batch. It has to be *observed* in the same place
every time — inside a tool, between pages, around a subprocess — and that code
cannot know which of those happened.

So the signal is a plain object with no framework behind it. It is not an
``asyncio.Event``, because tools are synchronous and a batch runner is threads.
It is not a Textual message or an HTTP disconnect callback, because ``core`` may
not import either. It is a token you check.

Two properties are worth stating because they are easy to get wrong:

**Nothing here starts a thread.** A deadline is observed when someone looks, not
by a timer firing in the background. A library that spawns a watchdog thread per
operation is a library that misbehaves inside somebody else's application, and
DocMax is importable — non-negotiable #1 in ``core/errors.py`` is the same idea
applied to ``sys.exit``.

**Cancellation is not failure.** :class:`~docmax.core.errors.CancelledError` is
``user_fixable = False`` and means the user got what they asked for. Combined
with the atomic writers, a cancelled operation leaves the destination exactly as
it was — so "stop" is always safe, and no interface has to warn about it.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from docmax.core.errors import CancelledError

if TYPE_CHECKING:
    from collections.abc import Callable


class CancellationToken:
    """A cooperative stop signal, checked rather than delivered.

    Passed down through an operation and consulted at points where stopping is
    safe::

        for page in pages:
            cancellation.raise_if_cancelled()
            ...

    Safe to share across threads: a batch runner cancels from its main thread
    while workers observe it from theirs.
    """

    __slots__ = ("_callbacks", "_deadline", "_event", "_lock", "_parent")

    def __init__(
        self,
        *,
        timeout: float | None = None,
        parent: CancellationToken | None = None,
    ) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []
        self._parent = parent
        #: Monotonic, so a clock adjustment mid-operation cannot move a deadline.
        self._deadline = None if timeout is None else time.monotonic() + timeout

    # -- observing ----------------------------------------------------------

    @property
    def is_cancelled(self) -> bool:
        """Whether work should stop, for any reason.

        Reading this is what makes a lapsed deadline take effect, which is the
        deliberate consequence of having no timer thread: a deadline stops the
        next checkpoint, not the current instruction.
        """
        if self._event.is_set():
            return True
        if self._parent is not None and self._parent.is_cancelled:
            self.cancel()
            return True
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self.cancel()
            return True
        return False

    def raise_if_cancelled(self, *, operation: str | None = None) -> None:
        """Stop here if asked to. The normal way to observe a token."""
        if not self.is_cancelled:
            return
        subject = f"{operation} was cancelled." if operation else "Cancelled."
        raise CancelledError(subject, context={"operation": operation} if operation else None)

    def remaining_seconds(self) -> float | None:
        """Time left before the deadline, or ``None`` when unbounded.

        Meant for handing to ``subprocess.run(timeout=...)``. Every external
        call needs a timeout — v2 had none anywhere, so a hung ``xelatex`` hung
        DocMax indefinitely — and this is how a deadline set once at the top
        reaches the process that has to honour it.

        Returns ``0.0`` rather than a negative number once the deadline has
        passed, since that is what a timeout argument expects.
        """
        deadlines = [
            token._deadline  # noqa: SLF001 — same class, and _deadline is monotonic
            for token in self._chain()
            if token._deadline is not None  # noqa: SLF001
        ]
        if not deadlines:
            return None
        return max(0.0, min(deadlines) - time.monotonic())

    # -- signalling ---------------------------------------------------------

    def cancel(self) -> None:
        """Ask for work to stop. Idempotent, and safe from any thread.

        Callbacks fire once, on the first call. A callback that raises is
        ignored: cancellation must succeed even if one of the things being torn
        down is already broken, and the exception a caller cares about is
        whatever they were cancelling, not the cleanup.
        """
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks = tuple(self._callbacks)
            self._callbacks.clear()

        for callback in callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 — see docstring
                pass

    def on_cancel(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register ``callback`` to run when cancellation is requested.

        This is how cancellation reaches something that cannot poll — killing a
        Ghostscript process, closing a socket. Returns a function that removes
        the registration, so a caller that finishes normally does not leak a
        reference to a process that has already exited::

            release = cancellation.on_cancel(process.kill)
            try:
                process.wait()
            finally:
                release()

        Registering on an already-cancelled token runs the callback immediately,
        because the alternative is a silent no-op at exactly the moment the
        caller was trying to guard against.
        """
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)

                def release() -> None:
                    with self._lock:
                        if callback in self._callbacks:
                            self._callbacks.remove(callback)

                return release

        callback()
        return _noop

    # -- composing ----------------------------------------------------------

    def child(self, *, timeout: float | None = None) -> CancellationToken:
        """A token cancelled by this one, and optionally sooner.

        For one document inside a batch: cancelling the batch stops the current
        file, while a per-file timeout does not touch the run. Deadlines
        accumulate down the chain rather than replacing each other, so a child
        can never outlive its parent by asking for longer.
        """
        return CancellationToken(timeout=timeout, parent=self)

    def _chain(self) -> tuple[CancellationToken, ...]:
        """This token and every ancestor, nearest first."""
        chain: list[CancellationToken] = []
        token: CancellationToken | None = self
        while token is not None:
            chain.append(token)
            token = token._parent  # noqa: SLF001 — same class
        return tuple(chain)

    def __repr__(self) -> str:
        state = "cancelled" if self._event.is_set() else "active"
        remaining = self.remaining_seconds()
        window = "" if remaining is None else f", {remaining:.1f}s left"
        return f"CancellationToken({state}{window})"


def _noop() -> None:
    """Releasing a registration that was never stored."""


class _NeverCancelled(CancellationToken):
    """The token for callers with nothing to cancel.

    Exists for the same reason as ``NullProgress``: so no engine is written with
    ``if cancellation is not None`` around every checkpoint, and so the checking
    code path is the one that runs in tests rather than a branch that is skipped.
    """

    __slots__ = ()

    @property
    def is_cancelled(self) -> bool:
        return False

    def cancel(self) -> None:
        """Ignore. A shared constant must not be cancellable by one caller."""

    def on_cancel(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Discard the registration rather than storing it.

        This object outlives every caller, so keeping callbacks it will never
        run would retain a reference to each one for the life of the process —
        a slow leak in the one token most likely to be used in a loop.
        """
        return _noop

    def child(self, *, timeout: float | None = None) -> CancellationToken:
        # Not parented to self: a real child of an uncancellable token is just a
        # normal token, and giving it a parent would make every check walk a
        # chain to reach a constant `False`.
        return CancellationToken(timeout=timeout)


#: Shared, immutable, and safe to pass anywhere a token is wanted.
NEVER_CANCELLED: CancellationToken = _NeverCancelled()


__all__ = ["NEVER_CANCELLED", "CancellationToken"]
