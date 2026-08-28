"""Which tools the TUI offers, and why that is not simply "all of them".

The registry is the source of truth and this module holds no descriptions, no
summaries and no parameter lists of its own — it reads
``core.registry.iter_tools`` exactly as the CLI, the server and the M10 MCP
server do. [ADR 0002](../../../docs/adr/0002-registry-mechanism.md) exists so
that adding tool #51 makes it appear here with no edit at all.

## The one thing it does hold

A registered tool is not necessarily a *runnable* one. ``ocr`` has a complete
``ToolSpec`` — both engines, three parameters — and a ``run()`` that raises
``NotImplementedError`` until M8. The CLI never exposed it, so nobody has met
that; a TUI that listed the registry verbatim would offer it, and selecting it
would produce an ``InternalError`` wrapping a ``NotImplementedError``. That is a
traceback-class failure for a condition we know about in advance, which the
error contract exists to prevent.

``ToolSpec`` cannot currently say "declared but not implemented", and adding a
field to it is a change to Core — which
[phases.md](../../../docs/planning/phases.md) says to report as a finding rather
than make in passing. So M7 names the exception here, in the interface that has
the problem, and ``tests/unit/test_tui.py`` asserts that this list and the set
of tools the CLI exposes stay identical. The moment ``ocr`` ships, deleting one
line here is the whole change — and if someone forgets, the test fails.

This is recorded as an open seam in
[current-status.md](../../../docs/planning/current-status.md); it is the fourth
instance of the same ``ToolSpec`` gap the project already owes an ADR.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from docmax.core.registry import ToolSpec

#: Registered, described, routable — and not yet implemented. See the module
#: docstring. This is expected to be empty again at M8.
UNIMPLEMENTED = frozenset({"ocr"})


def offered_tools() -> list[ToolSpec]:
    """Every tool the TUI will run, in the order it displays them.

    Sorted by category and then name, because a list of nineteen tools in pure
    alphabetical order puts ``compress`` between ``convert`` and ``crop`` and
    tells the user nothing about which of them are the same kind of thing.
    """
    return sorted(_runnable(), key=lambda spec: (spec.category, spec.name))


def categories() -> dict[str, list[ToolSpec]]:
    """The offered tools grouped by category, preserving the display order."""
    grouped: dict[str, list[ToolSpec]] = {}
    for spec in offered_tools():
        grouped.setdefault(spec.category, []).append(spec)
    return grouped


def is_offered(name: str) -> bool:
    """Is ``name`` a tool the TUI will run?"""
    return any(spec.name == name for spec in _runnable())


def _runnable() -> Iterator[ToolSpec]:
    from docmax.core.registry import iter_tools

    for spec in iter_tools():
        if spec.name not in UNIMPLEMENTED:
            yield spec


__all__ = ["UNIMPLEMENTED", "categories", "is_offered", "offered_tools"]
