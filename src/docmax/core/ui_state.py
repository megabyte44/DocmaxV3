"""Where the TUI's file browser last opened, remembered between runs.

Before this module existed, ``tui/browser.py``'s ``pick_files`` and
``pick_save_path`` always opened at ``default_start()`` — the user's home
directory — every single run (issue #29). Nothing remembered where a document
was actually chosen from last time, so anyone whose documents live a few
folders below home repeated the same navigation every session.

This is the small piece of state that fixes it: one directory, the folder a
file was most recently chosen from or saved to, remembered across runs the
same way ``core/consent.py`` remembers a grant — a separate app-owned JSON
file beside ``config.toml`` and ``consent.json``, located through
``core/config.py``'s ``ui_state_file()``.

**Scope: one global folder.** Not per-tool — a lookup keyed by tool name would
be exactly the kind of per-tool table CLAUDE.md rule 1 forbids, since nothing
about *this* file is a fact belonging to any one ``ToolSpec``. Not tracked
separately for opening versus saving, either: a person opens a document from a
folder and, more often than not, wants the save dialog to land somewhere in
that same neighbourhood next, so one "the folder I was just working in" value
serves both ``pick_files`` and ``pick_save_path``.

**Fails closed, harmlessly.** Unlike ``core/consent.py``, nothing recorded
here is privacy-sensitive — losing the record costs one extra trip to
``default_start()``, never a document sent somewhere it shouldn't be. So a
missing file, an unreadable one, one that is not valid JSON, a schema from a
future version, or a remembered directory that no longer exists (moved,
deleted, an unplugged drive) all collapse to the same ``None`` rather than
raising: the caller already has exactly one thing to do with any of them —
fall back to ``default_start()`` — so there is no reason to distinguish them
here.

``core`` may write nowhere outside ``core/atomic.py`` — CLAUDE.md rule 4 — so
:func:`save_last_directory` goes through ``atomic_write`` exactly as
``core/consent.py``'s own record does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from docmax.core.atomic import atomic_write
from docmax.core.models import OutputTarget

#: Schema of ``ui_state.json``, independent of ``consent.py``'s
#: ``SCHEMA_VERSION`` — the two files have nothing to do with each other and
#: must be free to evolve on their own.
SCHEMA_VERSION: Final = 1


def load_last_directory(path: Path) -> Path | None:
    """The folder a file was last chosen from or saved to, or ``None``.

    ``None`` covers every reason there is nothing usable to return — see the
    module docstring. The caller, ``tui/browser.py``, does not need to know
    which reason applied; it only needs to know whether to use this or fall
    back to ``default_start()``.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None

    try:
        document = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(document, dict) or document.get("version") != SCHEMA_VERSION:
        return None

    raw_directory = document.get("last_directory")
    if not isinstance(raw_directory, str) or not raw_directory:
        return None

    directory = Path(raw_directory)
    return directory if directory.is_dir() else None


def save_last_directory(path: Path, directory: Path) -> None:
    """Remember ``directory`` as the folder to open dialogs in next time.

    Written through ``core.atomic``, like every other write in this project,
    so a crash mid-write leaves the previously remembered folder in place
    rather than a truncated file that would then fail closed for no good
    reason.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {"version": SCHEMA_VERSION, "last_directory": str(directory)}
    payload = json.dumps(document, indent=2, sort_keys=False).encode("utf-8")
    with atomic_write(OutputTarget(destination=path, force=True)) as handle:
        handle.write(payload)
        handle.write(b"\n")


__all__ = ["SCHEMA_VERSION", "load_last_directory", "save_last_directory"]
