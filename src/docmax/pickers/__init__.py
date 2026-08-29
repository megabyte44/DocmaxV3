"""Parameter pickers — the narrow visual escape hatch from
[ADR 0005](../../docs/adr/0005-gui-pickers.md).

A few operations need a value that is genuinely unguessable without seeing the
page: where to crop, and what order pages should go in. A terminal cannot
express "drag a rectangle around this paragraph", so these two open a page in
the user's browser, wait, and hand back a value.

## The rule that makes this safe

**A picker returns parameters, never results.**

:func:`pick_box` does not crop. It renders the page, the user drags a box, and
it returns a :class:`~docmax.tools._box.PageBox`. That value then travels the
normal ``Tool`` → ``EngineRouter`` → ``atomic_write`` path like any other
parameter. There is no code path from anything in this package to an output
file, which is what makes ADR 0005's "never a general GUI shell" structural
rather than a promise: a parameter picker has nowhere to grow.

Three properties hold that line, and each is checked rather than intended:

* Nothing here writes — ``tests/hygiene/test_no_direct_writes.py`` covers this
  package, so a write outside ``core/atomic.py`` fails the build.
* Nothing here exits — ``tests/hygiene/test_no_sys_exit.py`` likewise.
* Nothing here imports an engine, a strategy or the router. The only document
  knowledge it borrows is ``tools/_pdf.py``'s read-only geometry, which is the
  module that already owns "open a PDF the same way in every tool".

## Where this sits

Below ``cli`` and ``tui`` and above ``tools``, because both interfaces reach for
it and neither may import the other. See
[ADR 0019](../../docs/adr/0019-picker-package-and-rendering.md), and
``.importlinter`` for the contract that enforces it.

## Every picker has a headless equivalent

``--box`` and ``--order`` ship, work over SSH, and are what the tests use.
``--interactive`` fills them in. Nothing in the project's behaviour depends on a
browser being present.
"""

from __future__ import annotations

from docmax.pickers.box import pick_box
from docmax.pickers.order import pick_order
from docmax.pickers.server import (
    DEFAULT_TIMEOUT_SECONDS,
    Choice,
    PickerCancelledError,
    run_picker,
)

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "Choice",
    "PickerCancelledError",
    "pick_box",
    "pick_order",
    "run_picker",
]
