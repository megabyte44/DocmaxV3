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
LIBRARY_PACKAGES = ("core", "tools", "cloud_client", "server")


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
