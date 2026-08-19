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
    from docmax.core.models import ToolResult

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


def render_result(result: ToolResult, *, dry_run: bool = False) -> None:
    """Report what an operation produced.

    Deliberately one line. The interesting output of a document tool is the
    document; a summary that scrolls the result off the screen is working
    against the user. Counts and timings live in ``result.details`` for the
    ``--json`` output that arrives at M6.

    The engine is named because it is the one thing a user cannot infer from
    looking at the file, and the one thing they may need to check — "did that
    run here, or did it go to the cloud?"
    """
    if dry_run:
        details = result.details
        out.print(
            f"[cyan]Dry run.[/cyan] Would use the "
            f"[bold]{result.engine_used.value}[/bold] engine "
            f"— {details.get('reason', 'no reason given')}."
        )
        out.print(f"  Would write: {details.get('destination', '—')}")
        return

    for path in result.outputs:
        # soft_wrap so a long path is not hard-wrapped mid-string. Rich would
        # otherwise break it across lines, and a path that cannot be
        # copy-pasted is worse than one that runs off the edge.
        out.print(f"[green]Wrote[/green] {path}", soft_wrap=True)

    summary = [f"{result.engine_used.value} engine"]
    pages = result.details.get("pages")
    if pages is not None:
        summary.append(f"{pages} pages")
    if result.duration_ms:
        summary.append(f"{result.duration_ms} ms")
    console.print(f"  [dim]{' · '.join(summary)}[/dim]")


__all__ = ["console", "out", "render_error", "render_result"]
