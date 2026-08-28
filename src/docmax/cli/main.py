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
from docmax.cli import cloud, commands, workflows
from docmax.cli.render import console, out
from docmax.core.branding import APP_NAME, CLI_NAME, HOMEPAGE
from docmax.core.models import Engine

app = typer.Typer(
    name=CLI_NAME,
    help=f"{APP_NAME} — terminal-native document toolkit. Local-first, no server required.",
    epilog=f"Docs: {HOMEPAGE}",
    add_completion=True,
    rich_markup_mode="rich",
    # Deliberately off. A bare `docmax` at an interactive terminal launches the
    # TUI; everywhere else it still prints help. `_no_command` below owns that
    # decision, because Typer's flag cannot express "unless there is a person
    # here" and getting it wrong would break `docmax | less` and every CI log.
    no_args_is_help=False,
    invoke_without_command=True,
)


def _version_callback(value: bool) -> None:
    if value:
        out.print(f"{APP_NAME} {__version__}")
        raise typer.Exit


@app.callback()
def main(
    ctx: typer.Context,
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

    if ctx.invoked_subcommand is None:
        _no_command(ctx)


def _no_command(ctx: typer.Context) -> None:
    """What a bare ``docmax`` does: start the TUI, or orient the user.

    ``tests/unit/test_cli.py`` has said since M0 that a bare invocation must
    never leave someone staring at nothing, and that at M7 it would launch the
    TUI. It does — **when there is a person at a terminal to use it.**

    Every other case still prints help, and each of the three guards is a real
    failure that the unguarded version would cause:

    * ``--json`` — a Textual app writes a screenful of escape sequences to
      stdout, which is precisely the stream
      [ADR 0017](../../../docs/adr/0017-json-output-contract.md) reserves for
      one JSON object.
    * not a terminal — ``docmax | less``, a CI step, a cron job. A TUI drawing
      into a pipe produces garbage and may never exit.
    * no ``textual`` — the extra is optional, and a bare command is the worst
      possible place to meet a missing-dependency error.

    Help is not a failure here, so the exit code is 0 either way.
    """
    from docmax.cli import json_output
    from docmax.cli.interactive import is_interactive

    if json_output.enabled() or not is_interactive() or not _tui_is_available():
        out.print(ctx.get_help())
        raise typer.Exit

    _launch_tui()


# Two one-line indirections so the three guards above can be tested without a
# real terminal and without a real Textual app. Importing inside them keeps
# `docmax --help` free of the TUI on a machine that has none.
def _tui_is_available() -> bool:
    from docmax.tui import is_available

    return is_available()


def _launch_tui() -> None:
    from docmax.tui import launch

    launch()


@app.command()
def tui(json_out: commands.JsonOption = False) -> None:
    """Open the interactive terminal interface.

    The same tools, the same router, the same engines — a second driver of the
    same core rather than a second implementation of anything. Every operation
    it offers is available as a command, and the reverse is true too.

    Needs the `tui` extra. Without it this reports the exact install line rather
    than an import error.
    """
    from docmax.cli import json_output
    from docmax.cli.interactive import require_interactive_is_possible
    from docmax.cli.render import render_error
    from docmax.core.errors import DocMaxError
    from docmax.tui import require_available

    # Accepted like every other command, and then refused — which is the point.
    # `--json` promises one machine-readable object on stdout, so the answer to
    # `tui --json` has to *be* that object, saying why there will be no TUI.
    json_output.note(json_out)

    try:
        require_interactive_is_possible(f"{CLI_NAME} tui")
        require_available()
        _launch_tui()
    except DocMaxError as exc:
        # Missing `textual`, `--json`, or no terminal. All three are anticipated
        # and all three get a message and a remedy, not a traceback.
        render_error(exc)
        raise typer.Exit(1) from exc


@app.command()
def mcp(
    root: Annotated[
        list[Path] | None,
        typer.Option(
            "--root",
            help="A directory the server may read and write. Repeatable. Default: the working directory.",
        ),
    ] = None,
    allow_cloud: Annotated[
        bool,
        typer.Option("--allow-cloud", help="Permit cloud engines for tools you already agreed to."),
    ] = False,
    json_out: commands.JsonOption = False,
) -> None:
    """Serve the tools over MCP, so an AI agent can drive DocMax locally.

    Speaks JSON-RPC on stdin and stdout, so it is started by an MCP client's
    configuration rather than by hand. Every tool the registry knows is offered,
    with a schema generated from its own declaration — the same router, the same
    validators, the same atomic writes as every other way in.

    **It may only touch what you allow.** Reads and writes are confined to
    `--root` (the working directory by default) and an agent cannot reach outside
    it. Existing files are never overwritten, and cloud engines are unavailable
    unless you pass `--allow-cloud` *and* have already agreed to that tool with
    `docmax cloud agree` — an agent cannot consent on your behalf.

    Needs the `mcp` extra. Without it this reports the exact install line rather
    than an import error.
    """
    from docmax.cli import json_output
    from docmax.cli.render import render_error
    from docmax.core.errors import DocMaxError, InvalidParameterError
    from docmax.mcp import require_available, serve

    # Accepted and then refused, exactly as `tui --json` is: stdout carries the
    # JSON-RPC stream, which is the one thing ADR 0017's single object cannot
    # share a channel with.
    json_output.note(json_out)

    try:
        if json_output.enabled():
            raise InvalidParameterError(
                f"`{CLI_NAME} mcp` speaks JSON-RPC on stdout, so --json cannot apply to it.",
                remedy=f"Run `{CLI_NAME} mcp` without --json; the protocol is the output.",
            )
        require_available()
        serve(root, allow_cloud=allow_cloud)
    except DocMaxError as exc:
        render_error(exc)
        raise typer.Exit(1) from exc


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

# The M9 composition commands — `pipeline`, `batch`, `watch` — join the same
# way. They are deliberately not in `commands.py`: that module is the per-tool
# commands, and these compose tools rather than being ones.
for _command in workflows.workflow_commands.registered_commands:
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
