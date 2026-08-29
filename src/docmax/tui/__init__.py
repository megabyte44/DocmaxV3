"""The Textual TUI — a second driver of the same core.

Not a replacement for the CLI and not built on it. ``cli``, ``tui``, ``server``
and the M10 ``mcp`` are peers: each turns some external protocol — argv,
keystrokes, HTTP, JSON-RPC — into a call on
:class:`~docmax.core.router.EngineRouter`, and turns the result back into
whatever that protocol expects. None of them may import another, and
``.importlinter``'s ``interfaces-are-independent`` contract is what enforces it.

What that buys, concretely: **there is no document logic in this package.** No
routing, no engine resolution, no consent rule, no validation, no atomic write,
no page count. The TUI knows how to draw a form from a
:class:`~docmax.core.registry.ToolSpec` and how to call the router. Everything
else was already built and is reached, not reimplemented.

## Optional, and honest about it

``textual`` lives in the ``tui`` extra, not the base install — the base is five
pure-Python packages and
[dependencies.md](../../docs/architecture/dependencies.md) keeps it that way.
:func:`is_available` answers with ``importlib.util.find_spec`` and never by
importing, which is the same discipline every ``EngineStrategy`` follows, and it
means ``docmax --help`` costs nothing on a machine that has no TUI installed.

Without the extra, ``docmax tui`` is a typed error naming the exact install
command. It is never an ``ImportError`` traceback.
"""

from __future__ import annotations

import importlib.util

from docmax.core.branding import DIST_NAME

#: The package, and the extra that carries it.
DEPENDENCY = "textual"
EXTRA = "tui"
INSTALL_HINT = f'pip install "{DIST_NAME}[{EXTRA}]"'


def is_available() -> bool:
    """Is Textual installed? Answered without importing it."""
    return importlib.util.find_spec(DEPENDENCY) is not None


def unavailable_reason() -> str | None:
    """Why the TUI cannot start, or ``None`` if it can."""
    if is_available():
        return None
    return f"{DEPENDENCY} is not installed."


def require_available() -> None:
    """Raise the typed error a missing ``textual`` deserves.

    ``LocalDependencyMissingError`` rather than something TUI-specific: it is
    the project's error for "the thing you asked for needs a package you do not
    have", it already carries the install hint as a field, and every interface
    already knows how to render it.
    """
    reason = unavailable_reason()
    if reason is None:
        return

    from docmax.core.errors import LocalDependencyMissingError

    raise LocalDependencyMissingError(
        f"The TUI needs the {DEPENDENCY!r} package, and {reason}",
        dependency=DEPENDENCY,
        install_hint=f"Install it with: {INSTALL_HINT}",
        context={"extra": EXTRA},
    )


def launch() -> None:
    """Start the TUI. Returns when the user quits.

    Imported inside the function, so this module — which ``cli/main.py`` reads
    in order to *check availability* — stays free of Textual.
    """
    require_available()

    from docmax.tui.app import DocMaxApp

    DocMaxApp().run()


__all__ = [
    "DEPENDENCY",
    "EXTRA",
    "INSTALL_HINT",
    "is_available",
    "launch",
    "require_available",
    "unavailable_reason",
]
