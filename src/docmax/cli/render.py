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


def render_metadata(result: ToolResult) -> None:
    """Print document metadata as a table.

    A table rather than a line, because this *is* the result — unlike the other
    tools, where the document is the result and the summary is a footnote.
    """
    from rich.table import Table

    fields = result.details.get("metadata", {})
    if not fields:
        console.print("[dim]No metadata.[/dim]")
        return

    table = Table(title="Document metadata", title_justify="left")
    table.add_column("Field")
    table.add_column("Value")
    for key in sorted(fields):
        table.add_row(str(key).lstrip("/"), str(fields[key]))
    out.print(table)


def render_permissions(result: ToolResult) -> None:
    """Print what `permissions` found, as a table of what is and is not allowed.

    The advisory note is printed every time rather than only for a restricted
    document. Someone who reads "Copy text: no" and stops there has learned the
    wrong thing, and the one line that corrects it costs nothing.
    """
    from rich.table import Table

    details = result.details
    table = Table(title=str(details.get("name", "")), title_justify="left")
    table.add_column("Permission")
    table.add_column("Allowed")
    table.add_column("What it covers")

    allowed = details.get("permissions", {})
    descriptions = details.get("descriptions", {})
    for name in allowed:
        table.add_row(
            name,
            "[green]yes[/green]" if allowed[name] else "[red]no[/red]",
            str(descriptions.get(name, "")),
        )

    out.print(table)

    if not details.get("encrypted"):
        console.print("  [dim]Not encrypted — a PDF with no encryption restricts nothing.[/dim]")
        return

    opened = details.get("opened_with")
    if opened == "owner":
        console.print(
            "  [dim]Opened with the owner password, which bypasses all of these anyway.[/dim]"
        )
    console.print("  [dim]These are advisory: nothing forces a reader to honour them.[/dim]")


def render_info(result: ToolResult) -> None:
    """Print what `get-info` found.

    Page count reads "unknown" rather than 0 for an encrypted file: the page
    tree genuinely cannot be read without the password, and reporting 0 would
    be a wrong answer rather than an absent one.
    """
    from rich.table import Table

    details = result.details
    table = Table(title=str(details.get("name", "")), title_justify="left")
    table.add_column("Property")
    table.add_column("Value")

    pages = details.get("pages")
    table.add_row("Pages", "unknown (encrypted)" if pages is None else str(pages))
    table.add_row("Size", f"{details.get('size_bytes', 0):,} bytes")
    table.add_row("Encrypted", "yes" if details.get("encrypted") else "no")

    fields = details.get("metadata", {})
    for key in sorted(fields):
        table.add_row(str(key).lstrip("/"), str(fields[key]))

    out.print(table)


__all__ = [
    "console",
    "out",
    "render_error",
    "render_info",
    "render_metadata",
    "render_permissions",
    "render_result",
]
