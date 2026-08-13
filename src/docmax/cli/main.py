"""CLI entry point.

This is the boundary where typed errors become exit codes. Library code raises;
only this layer calls ``typer.Exit``. ``tests/hygiene/test_no_sys_exit.py``
enforces the asymmetry by scanning ``core``, ``tools``, and ``cloud_client`` for
any process-terminating call.
"""

from __future__ import annotations

import shutil
from typing import Annotated

import typer

from docmax import __version__
from docmax.cli.render import console, out
from docmax.core.branding import APP_NAME, CLI_NAME, HOMEPAGE

app = typer.Typer(
    name=CLI_NAME,
    help=f"{APP_NAME} — terminal-native document toolkit. Local-first, no server required.",
    epilog=f"Docs: {HOMEPAGE}",
    add_completion=True,
    rich_markup_mode="rich",
    no_args_is_help=True,
)

#: External binaries a local engine may need, with the tools that depend on them.
#: ``docmax setup`` (M3) installs these; ``doctor`` only reports.
EXTERNAL_BINARIES: dict[str, tuple[str, ...]] = {
    "tesseract": ("ocr",),
    "gs": ("compress", "pdfa"),
    "pdftoppm": ("ocr", "to-images"),
    "pandoc": ("convert",),
}


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
def doctor() -> None:
    """Report the status of external tools the local engines depend on.

    Reports only — never mutates. ``docmax setup`` (M3) does the installing, and
    unlike v2's version it will be idempotent, support --dry-run, and verify
    afterwards rather than assuming success.
    """
    from rich.table import Table

    table = Table(title="External tool status", title_justify="left")
    table.add_column("Tool")
    table.add_column("Status")
    table.add_column("Path")
    table.add_column("Needed by")

    missing: list[str] = []
    for binary, used_by in EXTERNAL_BINARIES.items():
        path = shutil.which(binary)
        if path:
            table.add_row(binary, "[green]found[/green]", path, ", ".join(used_by))
        else:
            missing.append(binary)
            table.add_row(binary, "[yellow]missing[/yellow]", "—", ", ".join(used_by))

    out.print(table)

    if missing:
        console.print(
            f"\n[yellow]{len(missing)} tool(s) missing.[/yellow] "
            "Affected operations can still run via the Cloud Engine, "
            f"or install locally with [bold]{CLI_NAME} setup[/bold] (coming in M3)."
        )
    else:
        console.print("\n[green]All external tools available.[/green]")


if __name__ == "__main__":  # pragma: no cover
    app()
