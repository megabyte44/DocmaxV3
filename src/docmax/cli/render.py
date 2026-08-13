"""Terminal rendering. The only module that owns a ``rich.Console``.

v2 instantiated five separate ``Console`` objects across as many modules and
wrote to all of them from worker threads while a ``Progress`` live region was
active. The result was interleaved output and intermittent ``LiveError``
crashes. There is one console here, and from M9 onward worker threads will push
events onto a queue drained by a single render thread rather than printing
directly.

``core`` never imports this module, or ``rich`` at all — progress crosses that
boundary as the ``ProgressSink`` protocol.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

if TYPE_CHECKING:
    from docmax.core.errors import DocMaxError

#: stderr, so that ``docmax get-info x.pdf --json | jq`` stays clean.
console = Console(stderr=True)

#: stdout, for actual command results.
out = Console()


def render_error(exc: DocMaxError, *, as_json: bool = False) -> None:
    """Print a typed error as an actionable message — never a traceback.

    Every anticipated failure carries a remedy naming the next step. If a user
    sees a traceback, that is a bug in its own right: it means something escaped
    the typed hierarchy.
    """
    if as_json:
        out.print_json(json.dumps({"ok": False, "error": exc.to_dict()}))
        return

    body = Text()
    body.append(exc.message)
    if exc.remedy:
        body.append("\n\n")
        body.append("→ ", style="bold cyan")
        body.append(exc.remedy)

    console.print(
        Panel(
            body,
            title=f"[bold red]{exc.code.value}[/bold red]",
            border_style="red",
            padding=(0, 1),
        )
    )
