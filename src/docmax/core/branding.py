"""Product identity — the single source of truth.

This is the ONLY module permitted to contain brand literals. Every other module
imports from here, so a rename, a new cloud domain, or a change of display name
is a change to this file alone.

``tests/hygiene/test_branding.py`` enforces that: it scans the source tree and
fails if the brand appears anywhere it should not. A partial rename cannot pass CI.

The import name (``docmax``, i.e. the directory) and the PyPI distribution name
(``DIST_NAME``) are deliberately decoupled. They happen to match today; nothing
depends on them continuing to.
"""

from __future__ import annotations

from typing import Final

#: Human-readable name. Banners, window titles, prose.
APP_NAME: Final = "DocMax"

#: The executable. Used in ``argv[0]``, docs, and every error remedy that tells
#: a user what to run next.
CLI_NAME: Final = "docmax"

#: PyPI distribution name — the key for ``importlib.metadata.version()``.
#: Decoupled from ``CLI_NAME`` because plain ``docmax`` was already taken on
#: PyPI by the v2 package under this same author.
DIST_NAME: Final = "DocmaxV3"

#: Application directory name handed to ``platformdirs``. Resolves to
#: ``~/.config/docmax`` on Linux, ``~/Library/Application Support/DocMax`` on
#: macOS, ``%LOCALAPPDATA%\\DocMax`` on Windows.
#:
#: v2 shipped two config directories (``~/.DocMax`` and ``~/.docmax``), one of
#: which was created as an import side effect and never read. There is exactly
#: one now, and it is created lazily on first write.
CONFIG_DIR_NAME: Final = "docmax"

#: Prefix for every environment variable, e.g. ``DOCMAX_API_KEY``.
ENV_PREFIX: Final = "DOCMAX_"

#: Entry-point group third-party packages use to register their own tools.
#: See ``core/registry.py`` — adding a tool never requires editing a central file.
PLUGIN_GROUP: Final = "docmax.tools"

#: Default Cloud Engine endpoint. Overridable via config (``[cloud] endpoint``)
#: or ``DOCMAX_CLOUD_ENDPOINT``, so the hosted service is a convenient default
#: rather than a lock-in. Self-hosters point this at their own deployment.
DEFAULT_CLOUD_ENDPOINT: Final = "https://api.docmax.dev"

#: Project homepage, quoted in error remedies and ``--help`` epilogues.
HOMEPAGE: Final = "https://github.com/megabyte44/docmax"
