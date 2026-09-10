"""CLI entry point.

This is the boundary where typed errors become exit codes. Library code raises;
only this layer calls ``typer.Exit``. ``tests/hygiene/test_no_sys_exit.py``
enforces the asymmetry by scanning ``core``, ``tools``, and ``cloud_client`` for
any process-terminating call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from docmax import __version__
from docmax.cli import cloud, commands, mcp_group, workflows
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

# `mcp` is a group rather than a command: `docmax mcp` alone still serves, and
# `docmax mcp connect` wires a client to it instead of a person hand-editing
# that client's config from a README snippet. See `cli/mcp_group.py`.
app.add_typer(mcp_group.mcp_app)

# Token administration for a docmax.server deployment (ADR 0037) is
# deliberately *not* a subcommand here: docmax.server is excluded from the
# wheel (ADR 0006) and this package -- the one the base install ships -- may
# never import it (tests/hygiene/test_wheel_excludes_server.py). It lives at
# `python -m docmax.server.identity_cli`, run the same way the server itself
# is: from a checkout, with the `server` extra installed.


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

    Reports only — never mutates. ``docmax setup`` does the installing.
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
        f"or install locally with [bold]{CLI_NAME} setup[/bold]."
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


@app.command()
def setup(
    tool: Annotated[
        str | None,
        typer.Argument(
            help="Install only what this tool needs. Omit for everything missing.",
            show_default=False,
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the plan; install nothing.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
    json_out: commands.JsonOption = False,
) -> None:
    """Install what ``doctor`` reports missing: Python extras and system binaries.

    Installs only what is actually missing, so running this again once nothing
    is missing does nothing.

    **Never auto-elevates.** A package manager that needs ``sudo`` or an admin
    prompt fails with the exact command to re-run yourself, rather than DocMax
    attempting to grant itself a permission it was not given.

    **Never guesses at a second package manager.** Only the one manager this
    project already trusts for your platform (``apt-get``, ``brew``,
    ``winget`` — see ``tools/_binaries.py``) is ever run. When that manager is
    not on ``PATH``, this prints the same install line ``doctor`` already does
    instead of running anything.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import interruptible
    from docmax.cli.interactive import is_interactive
    from docmax.cli.render import _emit, render_error
    from docmax.core.cancellation import CancellationToken
    from docmax.core.errors import CancelledError, DocMaxError, InvalidParameterError
    from docmax.core.registry import get_tool, iter_tools
    from docmax.tools import _binaries, _install

    json_output.note(json_out)

    try:
        specs = [get_tool(tool)] if tool is not None else list(iter_tools())
    except DocMaxError as exc:
        render_error(exc)
        raise typer.Exit(1) from exc

    manager = _binaries.manager_available()
    plan = _install.build_plan(
        _install.missing_binaries(specs), _install.missing_extras(specs), manager
    )

    if not plan:
        if json_output.enabled():
            _emit(json_output.report({"items": [], "ran": False}))
        else:
            out.print("[green]Nothing missing.[/green]")
        return

    if not json_output.enabled():
        # `out`, not `console`: this is the answer a script wants, exactly as
        # `doctor`'s status table is -- the confirmation prompt and the
        # installer's own streamed chatter below are commentary and stay on
        # stderr.
        out.print("[bold]Plan:[/bold]")
        for item in plan:
            out.print(f"  {_setup_plan_line(item)}")

    if dry_run:
        if json_output.enabled():
            _emit(json_output.report({"items": plan, "ran": False}))
        return

    runnable = [item for item in plan if item["command"] is not None]
    if not runnable:
        if json_output.enabled():
            _emit(json_output.report({"items": plan, "ran": False}))
        else:
            console.print(
                "\n[yellow]Nothing here can be installed automatically on this "
                "platform.[/yellow] Run the commands above by hand."
            )
        raise typer.Exit(1)

    if not yes:
        if json_output.enabled() or not is_interactive():
            message = "Refusing to install without --yes: nothing to confirm with."
            if json_output.enabled():
                _emit(
                    json_output.failure(
                        InvalidParameterError(message, remedy=f"Re-run with {CLI_NAME} setup -y.")
                    )
                )
            else:
                console.print(f"\n[yellow]{message}[/yellow]")
            raise typer.Exit(1)
        console.print()
        if not typer.confirm("Proceed?", default=False):
            raise typer.Exit(1)

    token = CancellationToken()
    results: list[dict[str, Any]] = []
    with interruptible(token):
        try:
            for item in plan:
                if not json_output.enabled():
                    console.print(f"\n[bold]{item['name']}[/bold]:")
                on_output = None if json_output.enabled() else _print_unstyled
                results.append(
                    _install.run_item(
                        item, manager=manager, cancellation=token, on_output=on_output
                    )
                )
        except CancelledError as exc:
            console.print(f"\n[yellow]{exc.message}[/yellow]")
            raise typer.Exit(130) from exc

    if json_output.enabled():
        _emit(json_output.report({"items": results, "ran": True}))
    else:
        out.print()
        for result in results:
            out.print(f"  {_setup_result_line(result)}")

    if any(not result["verified"] for result in results):
        raise typer.Exit(1)


def _setup_plan_line(item: dict[str, Any]) -> str:
    used_by = ", ".join(item["used_by"])
    if item["command"] is not None:
        return f"{item['name']} (needed by {used_by}): {' '.join(item['command'])}"
    return f"{item['name']} (needed by {used_by}): {item['fallback']}"


def _print_unstyled(line: str) -> None:
    """Print one line of a package manager's own stdout.

    ``markup=False``: pip's real output includes literal unmatched brackets
    (``[notice] A new release of pip is available``) that Rich would otherwise
    try to parse as a style tag and reject.
    """
    console.print(line, markup=False)


def _setup_result_line(result: dict[str, Any]) -> str:
    from rich.markup import escape

    if result["verified"]:
        return f"[green]{result['name']}: installed[/green]"
    if result["command"] is None:
        return f"[yellow]{result['name']}: no package manager — {result['fallback']}[/yellow]"
    # escape(): this line is built for a markup-enabled console.print, and the
    # tail is the installer's own stdout -- which, like pip's "[notice] ..."
    # lines, can carry brackets Rich would otherwise try to parse as a tag.
    tail = f" {escape(result['stdout_tail'])}" if result.get("stdout_tail") else ""
    return f"[red]{result['name']}: failed[/red]{tail}"


if __name__ == "__main__":  # pragma: no cover
    app()
