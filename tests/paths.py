"""Shared filesystem locations for the hygiene suite."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "docmax"

#: Packages that are *library* code: importable, embeddable, and forbidden from
#: terminating the process or writing files outside the atomic helpers.
#: ``cli`` and ``tui`` are deliberately excluded — they are the layer that is
#: allowed to exit and to print. ``server`` is included: a request handler that
#: exits the process takes every other in-flight request with it.
#:
#: ``pickers`` is included too, and that is the whole enforcement behind
#: [ADR 0005](../docs/adr/0005-gui-pickers.md): a parameter picker "never opens
#: a document for writing, never touches the filesystem". Listing it here makes
#: a write outside ``core/atomic.py`` fail the build rather than fail review.
#:
#: ``runners`` (M9) is here for the sharper of the two reasons. v2's batch runner
#: was killed by a ``sys.exit`` inside an operation — ``SystemExit`` is not an
#: ``Exception``, so every ``except Exception`` in the loop missed it and one
#: missing dependency ended a 200-file run. The replacement loop is now itself
#: covered by ``test_no_sys_exit.py``. See ADR 0023.
#:
#: ``mcp`` (M10) is included for the reason ``server`` is: a tool handler that
#: terminated the process would take the client's whole session with it, and a
#: protocol server has even less excuse to write a file outside ``core/atomic.py``
#: than a request handler does. ``cli`` and ``tui`` remain excluded because they
#: are the layer that is *allowed* to exit. See ADR 0027.
LIBRARY_PACKAGES = ("core", "tools", "cloud_client", "server", "pickers", "runners", "mcp")


def library_sources() -> list[Path]:
    """Every ``.py`` file under the library packages."""
    files: list[Path] = []
    for package in LIBRARY_PACKAGES:
        files.extend(sorted((SRC / package).rglob("*.py")))
    return files


def all_sources() -> list[Path]:
    """Every ``.py`` file shipped in the package."""
    return sorted(SRC.rglob("*.py"))


def relative(path: Path) -> str:
    """Repo-relative path for readable assertion messages."""
    return str(path.relative_to(REPO_ROOT))
