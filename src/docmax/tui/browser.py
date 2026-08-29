"""Choosing files for the run screen's input and output fields, via the OS's own dialogs.

Typing a path by hand is how every field on ``RunScreen`` has worked since M7,
and for a short relative path that is fine. It stops being fine the moment the
document lives three directories away with a name nobody has memorised, and a
terminal offers no way to look — no file dialog, no autocomplete, nothing but
the keyboard.

This module is the fix. It hands the question to the operating system's own
native file-selection dialog — File Explorer on Windows, the Cocoa panel on
macOS, the desktop's own picker on Linux — and returns whatever the user chose.
It knows nothing about any tool. The only thing it is told is ``multiple`` —
whether the caller wants one path or several — and that comes from
:attr:`~docmax.core.registry.ToolSpec.accepts_multiple_inputs`, exactly as
every other generic thing on the form does. Adding tool #51 changes nothing
here, same as it changes nothing in ``forms.py``.

## Why ``tkinter`` and not a Textual widget

An earlier version of this module drew its own file browser inside the TUI, as
a modal `Textual` screen over a `DirectoryTree`. That worked, but it was a
second, TUI-specific file dialog living next to every native one the user's
desktop already has — its own keybindings, no thumbnails, no "recent files",
none of the affordances a real file-open dialog has. Replacing it with the
platform's own dialog is the more honest answer once the requirement is
literally "the OS's native picker."

``tkinter`` is what makes that possible **at no dependency cost**: it ships
with the standard CPython distribution for Windows and macOS, and
``tkinter.filedialog`` already delegates to each platform's native dialog —
File Explorer, the Cocoa open panel, the desktop portal on Linux — so this
module does not need one branch per platform. That is the same reasoning ADR
0005 used to choose ``http.server`` and ``webbrowser`` over pywebview or Qt for
the crop and reorder pickers: **both stdlib, zero new wheels, the OS already
provides the surface**. The risk ADR 0005 flagged for those alternatives —
"a leading source of Linux install failures" — applies to `tkinter` only in the
narrow case where Tk itself was not bundled with the interpreter (some
minimal Linux Python builds omit it) or no display is available (a
headless server with no X11/Wayland). :func:`pick_files` turns either failure
into :class:`~docmax.core.errors.LocalDependencyMissingError` — a typed,
actionable error, never a traceback — exactly as :func:`docmax.tui.require_available`
does for a missing ``textual``.

## Why this is not a ``pickers/`` picker

[ADR 0005](../../../docs/adr/0005-gui-pickers.md) and
[ADR 0019](../../../docs/adr/0019-picker-package-and-rendering.md) built a real
mechanism for "a parameter a terminal cannot ask for" — but it is a mechanism
for *spatial* parameters on a document that has *already been chosen*: it opens
a browser tab, renders one page of a known PDF, and waits for coordinates or an
order. Choosing which document to open in the first place is the opposite
problem — there is no document yet to render — and the OS already has a purpose
-built, zero-dependency answer for it that owes nothing to a local HTTP server
or a browser tab.

## What it does not do

It does not validate that a chosen file is the right *kind* of input for the
tool — that is what the router's ``DocumentRef.from_path`` and each tool's own
checks are for, and duplicating them here is how two implementations of the
same rule start to disagree. Likewise for the save dialog and the output
field: ``OutputTarget.resolve`` remains the sole authority on whether a chosen
destination collides with an input, already exists, or sits in a writable
directory — this module hands back a string the user typed into a native
dialog, nothing more. It only returns filesystem paths; the normal execution
path decides whether they are usable. It never opens a file for reading or
writing and never creates one — a save dialog returning a path is not the same
as writing to it, and neither `asksaveasfilename` nor anything here does the
latter.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter
    from collections.abc import Iterator, Sequence


def render_paths(paths: Sequence[Path]) -> str:
    """The comma-separated text the ``__inputs__`` field expects.

    A plain function so the one bit of string-shaping this module does can be
    tested without a dialog or a display.
    """
    return ", ".join(str(path) for path in paths)


def merge_paths(existing: str, chosen: Sequence[Path]) -> str:
    """The ``__inputs__`` text after adding ``chosen`` to what was already there.

    A multi-input tool's field is filled in one trip to the dialog at a time —
    one document now, another later — and each trip must add to the field
    rather than replace it, or the second trip would silently discard the
    first. Existing entries keep both their order and their position: a path
    already present is left where it is instead of being moved to the end,
    which is what makes selecting the same file again a no-op rather than a
    duplicate. New paths are appended in the order they were chosen.

    Parses ``existing`` the same way ``RunScreen._request`` does — split on
    commas, strip, drop blanks — so this reads the field exactly as the code
    that later reads it for real does, rather than as a second, possibly
    disagreeing implementation of the same parsing.
    """
    current = [Path(part.strip()) for part in existing.split(",") if part.strip()]
    for path in chosen:
        if path not in current:
            current.append(path)
    return render_paths(current)


def default_start() -> Path:
    """Where the dialog opens with no other opinion: the user's home directory.

    Not the process's working directory — a TUI started from a project
    checkout would otherwise open the dialog inside that checkout, which is
    the wrong place to go looking for a document. ``Path.home()`` is stdlib and
    resolves the platform's own notion of home (``$HOME``, ``%USERPROFILE%``),
    which is what puts Desktop, Documents and Downloads one step away without
    naming any of them — naming one would be the Windows-specific assumption
    the other two platforms do not share.

    Falls back to the working directory only if the platform cannot say what
    home is at all — an unusual environment, not a reason to refuse to open.
    """
    try:
        return Path.home()
    except RuntimeError:
        return Path.cwd()


@contextmanager
def _hidden_root() -> Iterator[tkinter.Tk]:
    """A withdrawn, always-on-top Tk root, ready to parent one dialog.

    Every dialog in this module goes through here, so there is exactly one
    place that creates a Tk root and exactly one place that turns its two
    failure modes — Tk not installed, no display available — into a typed
    :class:`~docmax.core.errors.LocalDependencyMissingError` rather than
    letting an ``ImportError`` or a ``TclError`` reach the user as a
    traceback. The root is destroyed on the way out either way, so a dialog
    that raises never leaks a window.
    """
    try:
        import tkinter as tk
    except ImportError as exc:
        from docmax.core.errors import LocalDependencyMissingError

        raise LocalDependencyMissingError(
            "The file browser needs Tk, and it is not installed.",
            dependency="tkinter",
            install_hint="Install your platform's Tcl/Tk package, or type the path instead.",
        ) from exc

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        from docmax.core.errors import LocalDependencyMissingError

        raise LocalDependencyMissingError(
            "The file browser needs a display, and none is available.",
            dependency="tkinter",
            install_hint="Type the path instead — this dialog needs a desktop session.",
        ) from exc

    try:
        root.withdraw()
        root.attributes("-topmost", True)
        yield root
    finally:
        root.destroy()


def _native_dialog(*, multiple: bool, start: Path) -> tuple[str, ...] | str:
    """Ask the OS's native *open* dialog for one or more paths to read.

    Kept small and separate so a test can replace it with a canned answer —
    the same reason ``pickers/`` tests reach for an ``announce`` callback
    instead of a real browser: nothing here can be driven by simulated
    keypresses the way a Textual widget can, because it is a window the OS
    owns, not one Textual draws.
    """
    from tkinter import filedialog

    with _hidden_root() as root:
        if multiple:
            return filedialog.askopenfilenames(
                title="Choose one or more files", initialdir=str(start), parent=root
            )
        return filedialog.askopenfilename(title="Choose a file", initialdir=str(start), parent=root)


def _native_save_dialog(*, start: Path) -> str:
    """Ask the OS's native *save* dialog for one path to write to.

    The save-dialog counterpart of :func:`_native_dialog`, and deliberately a
    separate function rather than a third branch of it: a test that wants to
    replace "the user picked an existing file to read" must not also have to
    know about "the user typed a new filename to write" and vice versa. Like
    every dialog here, this only asks a question and returns a string —
    ``tkinter.filedialog.asksaveasfilename`` does not create, open or touch
    the path it returns.
    """
    from tkinter import filedialog

    with _hidden_root() as root:
        return filedialog.asksaveasfilename(
            title="Choose where to save the result", initialdir=str(start), parent=root
        )


def pick_files(*, multiple: bool, start: Path | None = None) -> list[Path] | None:
    """Open the OS's native open dialog and return what was chosen.

    Returns ``None`` if the user cancelled without choosing anything — Tk's own
    convention is an empty string for a single dialog and an empty tuple (or,
    on some platform/version combinations, an empty string) for a multi-select
    one, and both collapse to the same ``None`` here so the caller has one
    thing to check regardless of ``multiple``.

    Never opens, reads or writes the chosen file itself: it only asks the
    dialog for paths and hands them back as :class:`~pathlib.Path` objects.
    """
    root_dir = start if start is not None and start.is_dir() else default_start()
    chosen = _native_dialog(multiple=multiple, start=root_dir)

    if not chosen:
        return None
    if isinstance(chosen, str):
        return [Path(chosen)]
    return [Path(item) for item in chosen if item]


def pick_save_path(*, start: Path | None = None) -> Path | None:
    """Open the OS's native save dialog and return the chosen path.

    Returns ``None`` if the user cancelled — Tk's convention for a cancelled
    save dialog is an empty string. Never creates, opens or writes the file
    itself; see the module docstring. ``start`` lets a caller open the dialog
    somewhere more useful than the home directory (``RunScreen`` uses the
    first input's own folder, when there is one, which is what puts the
    dialog exactly where the worked example in the feature request expects
    it — the folder the inputs already live in), but the choice of directory
    is never itself a destination: the user still names the file.
    """
    root_dir = start if start is not None and start.is_dir() else default_start()
    chosen = _native_save_dialog(start=root_dir)
    return Path(chosen) if chosen else None


__all__ = ["default_start", "merge_paths", "pick_files", "pick_save_path", "render_paths"]
