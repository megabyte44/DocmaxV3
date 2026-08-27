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
from docmax.cli import cloud, commands
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
    json_out: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit one JSON object on stdout. Diagnostics go to stderr.",
        ),
    ] = False,
) -> None:
    """Root callback. Global options live here.

    ``--json`` is global rather than per command so it is spelled the same way
    everywhere and a new command inherits it. It is recorded once here, before
    any command body runs, and read by the renderers — see
    ``cli/json_output.py`` for why that is a module-level switch rather than an
    argument threaded through eighteen signatures.
    """
    from docmax.cli import json_output

    json_output.set_enabled(json_out)


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
    json_out: commands.JsonOption = False,
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
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "merge",
        inputs,
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        outline=outline,
    )
    render_result(result, dry_run=dry_run, tool="merge")


# The M2 tool commands live in their own module so this file stays the
# application shell. Registered rather than imported one by one, so adding a
# tool command touches `commands.py` alone.
for _command in commands.app_commands.registered_commands:
    app.registered_commands.append(_command)

# `cloud` is a group rather than a command: credentials and consent are a
# handful of related verbs, and flattening them would put `docmax login` next to
# `docmax merge` as though they were the same kind of thing.
app.add_typer(cloud.cloud_app)


@app.command()
def formats(json_out: commands.JsonOption = False) -> None:
    """List the formats each tool can read and write.

    The command `UnsupportedFormatError` has told users to run since M0. It
    renders `tools/_formats.py` and holds no list of its own, so what is printed
    here and what a tool actually accepts cannot disagree — see
    [ADR 0010](https://github.com/megabyte44/docmax/blob/main/docs/adr/0010-format-vocabulary.md).

    Formats DocMax knows about but cannot use are listed too, with the reason.
    "Unknown format" is a much worse answer than "here is why not".
    """
    from docmax.cli import json_output
    from docmax.cli.render import render_formats

    json_output.note(json_out)
    render_formats()


@app.command()
def doctor(json_out: commands.JsonOption = False) -> None:
    """Report the status of external tools the local engines depend on.

    Reports only — never mutates. ``docmax setup`` (M3) does the installing, and
    unlike v2's version it will be idempotent, support --dry-run, and verify
    afterwards rather than assuming success.
    """
    from rich.table import Table

    from docmax.cli import json_output
    from docmax.tools import _binaries

    json_output.note(json_out)
    if json_output.enabled():
        _emit_doctor_json()
        return

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


def _emit_doctor_json() -> None:
    """`doctor` as data: the same declaration the table renders.

    A script checking "is Ghostscript installed before I start a batch" wants
    this, and parsing the table would be worse for both of us.
    """
    from docmax.cli import json_output
    from docmax.cli.render import _emit
    from docmax.tools import _binaries

    _emit(
        json_output.report(
            {
                "binaries": [
                    {
                        "name": binary.name,
                        "found": _binaries.find(binary.name),
                        "used_by": list(binary.used_by),
                        "install_hint": binary.install_hint(),
                    }
                    for binary in _binaries.EXTERNAL_BINARIES
                ]
            }
        )
    )


if __name__ == "__main__":  # pragma: no cover
    app()
