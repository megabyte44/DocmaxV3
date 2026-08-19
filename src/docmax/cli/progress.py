"""The terminal's implementation of ``ProgressSink``.

``core`` reports progress through three method calls and knows nothing about
what renders them. This is the terminal's answer; the TUI, the server and the
MCP server will each write their own, and no engine will change.

Two properties the protocol demands, and the reasons they matter here:

**It must never raise.** A failure to *report* progress is never a reason to
fail an operation that is otherwise succeeding — a broken progress bar must not
lose someone's merged document. Every method is wrapped accordingly.

**It must tolerate a worker thread.** Rich's live display is not reentrant, and
v2 crashed with intermittent ``LiveError`` because five ``Console`` objects wrote
into an active live region from threads. There is one console here, and the
guard below means a second ``start`` reuses the region rather than opening a
competing one.

The bar disables itself when stdout is not a terminal, so piping into a file or
a CI log yields output rather than a screenful of escape sequences.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType

    from rich.console import Console
    from rich.progress import Progress, TaskID


class ConsoleProgress:
    """A Rich progress bar behind the framework-independent protocol.

    Satisfies ``ProgressSink`` structurally — it declares no base class, so
    nothing here creates an import edge back into ``core``.
    """

    __slots__ = ("_console", "_progress", "_task")

    def __init__(self, console: Console) -> None:
        self._console = console
        self._progress: Progress | None = None
        self._task: TaskID | None = None

    def start(self, description: str, *, total: int | None = None) -> None:
        """Open the live region, or add a task to the one already open."""
        with suppress(Exception):
            if self._progress is None:
                self._progress = self._build()
                self._progress.start()
            self._task = self._progress.add_task(description, total=total)

    def advance(self, amount: int = 1) -> None:
        with suppress(Exception):
            if self._progress is not None and self._task is not None:
                self._progress.advance(self._task, amount)

    def finish(self) -> None:
        """Close the live region.

        Called from the router's ``finally``, so it must be safe after a failure
        and safe when ``start`` was never reached — and safe twice, since a
        caller using this as a context manager will reach it again on exit.
        """
        with suppress(Exception):
            if self._progress is not None:
                self._progress.stop()
        self._progress = None
        self._task = None

    def _build(self) -> Progress:
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
        )

        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self._console,
            # Cleared on completion: the result line is what the user keeps, and
            # a finished bar left on screen is noise in a scrollback.
            transient=True,
            # Silent when piped. A CI log does not want escape sequences, and
            # `docmax merge ... > out.log` should not fill it with redraws.
            disable=not self._console.is_terminal,
        )

    # -- convenience --------------------------------------------------------

    def __enter__(self) -> ConsoleProgress:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.finish()


__all__ = ["ConsoleProgress"]
