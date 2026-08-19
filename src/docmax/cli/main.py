"""CLI entry point.

This is the boundary where typed errors become exit codes. Library code raises;
only this layer calls ``typer.Exit``. ``tests/hygiene/test_no_sys_exit.py``
enforces the asymmetry by scanning ``core``, ``tools``, and ``cloud_client`` for
any process-terminating call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from docmax import __version__
from docmax.cli import commands
from docmax.cli.render import console, out
from docmax.core.branding import APP_NAME, CLI_NAME, HOMEPAGE
from docmax.core.models import Engine

app = typer.Typer(
    name=CLI_NAME,
    help=f"{APP_NAME} — terminal-native document toolkit. Local-first, no server required.",
    epilog=f"Docs: {HOMEPAGE}",
    add_completion=True,
    rich_markup_mode="rich",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        out.print(f"{APP_NAME} {__version__}")
        raise typer.Exit


@app.callback()
def main(
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
) -> None:
    """Root callback. Global options live here."""


@app.command()
def merge(
    inputs: Annotated[
        list[Path],
        typer.Argument(
            help="PDFs to combine, in the order they should appear.",
            show_default=False,
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Where to write the merged PDF. Required.",
            show_default=False,
        ),
    ],
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite the output if it already exists."),
    ] = False,
    outline: Annotated[
        bool,
        typer.Option("--outline/--no-outline", help="Add a bookmark per source file."),
    ] = True,
    engine: Annotated[
        Engine | None,
        typer.Option("--engine", help="Force an engine. Default: decide from availability."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Say what would happen, and write nothing."),
    ] = False,
) -> None:
    """Combine several PDFs into one, in the order given.

    ``-o`` is required, and deliberately so. Every other tool can derive a
    destination from its first input by swapping the extension — but merge's
    inputs and its output are all PDFs, so a derived name would land exactly on
    the first input and destroy it. That is the single most destructive bug v2
    shipped. `OutputTarget` would refuse it anyway; requiring the flag means the
    user is told at the boundary instead of after typing a command that could
    never have worked.
    """
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    result = execute(
        "merge",
        inputs,
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        outline=outline,
    )
    render_result(result, dry_run=dry_run)


# The M2 tool commands live in their own module so this file stays the
# application shell. Registered rather than imported one by one, so adding a
# tool command touches `commands.py` alone.
for _command in commands.app_commands.registered_commands:
    app.registered_commands.append(_command)


@app.command()
def doctor() -> None:
    """Report the status of external tools the local engines depend on.

    Reports only — never mutates. ``docmax setup`` (M3) does the installing, and
    unlike v2's version it will be idempotent, support --dry-run, and verify
    afterwards rather than assuming success.
    """
    from rich.table import Table

    from docmax.tools import _binaries

    table = Table(title="External tool status", title_justify="left")
    table.add_column("Tool")
    table.add_column("Status")
    table.add_column("Path")
    table.add_column("Needed by")

    missing: list[_binaries.Binary] = []
    for binary in _binaries.EXTERNAL_BINARIES:
        used_by = ", ".join(binary.used_by)
        found = _binaries.find(binary.name)
        if found:
            table.add_row(binary.name, "[green]found[/green]", found, used_by)
        else:
            missing.append(binary)
            table.add_row(binary.name, "[yellow]missing[/yellow]", "—", used_by)

    out.print(table)

    if not missing:
        console.print("\n[green]All external tools available.[/green]")
        return

    console.print(f"\n[yellow]{len(missing)} tool(s) missing.[/yellow]")
    for binary in missing:
        # The exact install command, per platform. That is the difference
        # between a report and something the user can act on.
        console.print(f"  [bold]{binary.name}[/bold] — {binary.install_hint()}")
    console.print(
        "\nAffected operations can still run via the Cloud Engine (M6), "
        f"or install locally with [bold]{CLI_NAME} setup[/bold] (coming later)."
    )


if __name__ == "__main__":  # pragma: no cover
    app()
