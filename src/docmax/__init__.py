"""DocMax — terminal-native document toolkit.

Importing this package must stay cheap. It pulls in no heavy dependency, no
tool implementation, and no UI framework; ``tests/hygiene/test_no_heavy_imports.py``
asserts as much by checking ``sys.modules`` after a bare import.

That matters because non-negotiable #3 says the base install stays light: the
TUI shell and cloud client are all a user gets until they first invoke a local
engine that genuinely needs more.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from docmax.core.branding import DIST_NAME

try:
    __version__ = version(DIST_NAME)
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
