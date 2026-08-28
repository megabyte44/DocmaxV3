"""A progress sink that says which item, in front of whatever the tool says.

Every strategy calls ``progress.start()`` with its own description — "Recognising
12 of 40 page(s)", "Compressing 8 page(s) with Ghostscript". That is the right
thing for one document and not enough for two hundred: the user needs to know
which document, and the tool is the only thing that knows the rest.

``ProgressSink`` has no way to say "and by the way this is item 7 of 200", and
adding one would be a Core change for a cosmetic reason. So the runners wrap the
caller's sink instead and prefix a label onto whatever description passes
through. The inner sink is untouched, the protocol is unchanged, and the TUI or
the server gets the same behaviour without knowing this exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docmax.core.protocols import ProgressSink


class LabelledProgress:
    """Prefixes ``label`` onto every description the wrapped sink is given.

    Satisfies ``ProgressSink`` structurally, like every other implementation in
    this project — declaring a base class would create an import edge into
    ``core`` for no benefit.

    Never raises, for the reason ``core/protocols.py`` gives: failing to *report*
    progress must never fail an operation that is otherwise succeeding.
    """

    __slots__ = ("_inner", "_label")

    def __init__(self, inner: ProgressSink, label: str) -> None:
        self._inner = inner
        self._label = label

    def start(self, description: str, *, total: int | None = None) -> None:
        self._inner.start(f"{self._label} {description}".strip(), total=total)

    def advance(self, amount: int = 1) -> None:
        self._inner.advance(amount)

    def finish(self) -> None:
        self._inner.finish()


__all__ = ["LabelledProgress"]
