"""The MCP interface — DocMax driven by an agent, locally.

The fourth interface, and the fourth driver of one core. `cli` turns argv into a
router call, `tui` turns keystrokes, `server` turns HTTP, and this turns
JSON-RPC over stdio. None of them may import another, and none contains document
logic.

**This package root exposes exactly three things**, mirroring `docmax.tui`'s
three: :func:`is_available`, :func:`require_available` and :func:`serve`. That
narrowness is the whole of ADR 0027's entry-point allowance — `docmax.cli.main`
may import *this module* to start the server, and anything reaching for
`docmax.mcp.server`, `.schema` or `.policy` from the CLI is the CLI reaching into
another interface's internals, which stays forbidden and is separately asserted.

Nothing here imports the SDK. `mcp` is an optional extra (ADR 0027), it is in
`HEAVY_MODULES`, and `docmax mcp --help` must work on a machine that does not
have it — so the import lives inside :func:`serve`, exactly as `docmax.tui`'s
`textual` import does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmax.core.branding import CLI_NAME, DIST_NAME
from docmax.core.errors import LocalDependencyMissingError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

#: The extra that carries the SDK, the distribution it installs, and the import
#: that proves it is there. Spelled out separately because they are three
#: different things that happen to share a name — the extra, the PyPI package,
#: and a module inside it.
EXTRA = "mcp"
DEPENDENCY = "mcp"
INSTALL_HINT = f'pip install "{DIST_NAME}[{EXTRA}]"'
_SDK_MODULE = "mcp.server.lowlevel"


def is_available() -> bool:
    """True when the MCP SDK can be imported.

    Checked with ``find_spec`` rather than by importing, per the heavy-imports
    rule: asking whether a dependency exists must not be the act that loads it.
    """
    import importlib.util

    try:
        return importlib.util.find_spec(_SDK_MODULE) is not None
    except (ImportError, ValueError):  # pragma: no cover — a broken installation
        return False


def require_available() -> None:
    """Raise a typed error naming the install line, or return.

    A missing optional dependency is an anticipated condition with an obvious
    remedy, so it gets one — never an ``ImportError`` traceback at the worst
    possible moment.
    """
    if is_available():
        return
    raise LocalDependencyMissingError(
        f"`{CLI_NAME} {EXTRA}` needs the {DEPENDENCY!r} package, which is not installed.",
        dependency=DEPENDENCY,
        install_hint=f"Install it with: {INSTALL_HINT}",
        context={"extra": EXTRA},
    )


def serve(roots: Sequence[Path] | None = None, *, allow_cloud: bool = False) -> None:
    """Run the stdio MCP server until the client disconnects.

    ``roots`` defaults to the working directory — never to "everywhere", because
    a default that is safe only once configured is not a safe default. See
    ADR 0029.
    """
    require_available()

    import anyio

    from docmax.mcp.policy import Policy
    from docmax.mcp.server import serve_stdio

    policy = Policy.build(roots, allow_cloud=allow_cloud)
    anyio.run(serve_stdio, policy)


__all__ = ["DEPENDENCY", "EXTRA", "INSTALL_HINT", "is_available", "require_available", "serve"]
