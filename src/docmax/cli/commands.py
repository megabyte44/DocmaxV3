"""The M2 tool commands.

Kept out of ``main.py`` so that file stays the application shell — the app
object, global options, and `doctor`. Each command here does the same three
things and nothing else: name its arguments, hand them to
:func:`docmax.cli.execution.execute`, and render the result.

**No routing decisions live here.** ``--engine`` is forwarded as a request and
absent forwards nothing; the router applies the precedence ladder. A command
that grew an `if offline` would be the start of a second implementation of a
rule that must never differ, and ``test_cli_merge.py`` asserts structurally that
none has.

## Read-only commands

``get-info`` and a bare ``metadata`` write nothing, but ``EngineStrategy.run``
requires an ``OutputTarget``. Those two build one directly rather than through
``router.target_for``, because resolution would check a destination that is
never written — and would refuse the run with ``OutputExistsError`` if a file
happened to sit at the derived path. See the note in ``tools/get_info/local.py``:
the clean fix is a ``produces_output`` flag on ``ToolSpec``, which is a core
change and is being reported rather than made.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from docmax.core.models import Engine

app_commands = typer.Typer()

# Shared option definitions, so `--engine` means the same thing everywhere and
# gains a behaviour in one place rather than seven.
_EngineOption = Annotated[
    Engine | None,
    typer.Option("--engine", help="Force an engine. Default: decide from availability."),
]
_ForceOption = Annotated[
    bool,
    typer.Option("--force", "-f", help="Overwrite the output if it already exists."),
]
_DryRunOption = Annotated[
    bool,
    typer.Option("--dry-run", help="Say what would happen, and write nothing."),
]
_PagesOption = Annotated[
    str | None,
    typer.Option("--pages", help="Which pages, e.g. 1-3,7. Default: all."),
]


@app_commands.command()
def split(
    source: Annotated[Path, typer.Argument(help="The PDF to split.", show_default=False)],
    output: Annotated[
        Path,
        typer.Option(
            "--output", "-o", help="Directory to write the parts into.", show_default=False
        ),
    ],
    every: Annotated[
        int, typer.Option("--every", help="Pages per output file. 1 gives one file per page.")
    ] = 1,
    pages: _PagesOption = None,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
) -> None:
    """Split a PDF into several files.

    The output is a **directory**, staged and swapped in as a unit — a cancelled
    run leaves the destination exactly as it was rather than holding the first
    few parts.
    """
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    result = execute(
        "split",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        every=every,
        pages=pages,
    )
    render_result(result, dry_run=dry_run)


@app_commands.command()
def rotate(
    source: Annotated[Path, typer.Argument(help="The PDF to rotate.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    by: Annotated[int, typer.Option("--by", help="Degrees clockwise: 90, 180 or 270.")] = 90,
    pages: _PagesOption = None,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
) -> None:
    """Rotate pages by a multiple of 90 degrees."""
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    result = execute(
        "rotate",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        by=by,
        pages=pages,
    )
    render_result(result, dry_run=dry_run)


@app_commands.command()
def pages(
    source: Annotated[Path, typer.Argument(help="The PDF to work on.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    select: Annotated[
        str | None, typer.Option("--select", help="Pages to keep, e.g. 1-3,7.")
    ] = None,
    delete: Annotated[str | None, typer.Option("--delete", help="Pages to remove, e.g. 4.")] = None,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
) -> None:
    """Keep or delete selected pages.

    ``--select`` and ``--delete`` are two ways of saying the same thing and
    cannot be combined; the tool refuses rather than inventing a precedence.
    """
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    result = execute(
        "pages",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        select=select,
        delete=delete,
    )
    render_result(result, dry_run=dry_run)


@app_commands.command()
def reorder(
    source: Annotated[Path, typer.Argument(help="The PDF to reorder.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    order: Annotated[
        str,
        typer.Option("--order", help="The new order, e.g. 3,1,2.", show_default=False),
    ],
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
) -> None:
    """Reorder pages into a given sequence.

    The order must list every page exactly once. A reorder that quietly dropped
    or duplicated a page would be discovered far too late.
    """
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    result = execute(
        "reorder", [source], output, engine=engine, force=force, dry_run=dry_run, order=order
    )
    render_result(result, dry_run=dry_run)


@app_commands.command()
def sanitize(
    source: Annotated[Path, typer.Argument(help="The PDF to clean.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
) -> None:
    """Remove JavaScript, embedded files and auto-actions.

    Removes document-level JavaScript and embedded files, `/OpenAction`,
    `/AcroForm`, and page-level `/AA` and `/Annots`. It does **not** inspect
    content streams and is no defence against a PDF crafted to attack a viewer's
    parser. Outlines and links are lost along with the actions.
    """
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    result = execute("sanitize", [source], output, engine=engine, force=force, dry_run=dry_run)
    render_result(result, dry_run=dry_run)


@app_commands.command()
def compress(
    source: Annotated[Path, typer.Argument(help="The PDF to compress.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    preset: Annotated[
        str, typer.Option("--preset", help="Ghostscript preset: screen, ebook, printer, prepress.")
    ] = "ebook",
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
) -> None:
    """Shrink a PDF with Ghostscript.

    Needs Ghostscript installed — run `docmax doctor` to check, and it will
    print the install line for your platform. The page count is verified before
    the result replaces anything, because a smaller file with fewer pages is not
    a compressed document.
    """
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    result = execute(
        "compress",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        preset=preset,
    )
    render_result(result, dry_run=dry_run)


@app_commands.command()
def metadata(
    source: Annotated[Path, typer.Argument(help="The PDF to inspect.", show_default=False)],
    set_: Annotated[
        list[str] | None,
        typer.Option("--set", help="Field=Value to write. Repeatable. Omit to read."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Where to write. Required when setting."),
    ] = None,
    clear: Annotated[
        bool, typer.Option("--clear", help="Remove every field before writing.")
    ] = False,
    force: _ForceOption = False,
    engine: _EngineOption = None,
) -> None:
    """Read or set document metadata.

    With no `--set` it reads and writes nothing. With `--set`, `-o` is required:
    the result is a new document, and this tool will not edit a file in place.
    """
    from docmax.cli.execution import execute, execute_read_only
    from docmax.cli.render import render_metadata, render_result

    writing = bool(set_) or clear
    if writing and output is None:
        raise typer.BadParameter("-o is required when setting metadata.", param_hint="--output")
    if not writing and output is not None:
        raise typer.BadParameter("-o is only used with --set or --clear.", param_hint="--output")

    if writing:
        assert output is not None  # narrowed by the check above
        result = execute(
            "metadata",
            [source],
            output,
            engine=engine,
            force=force,
            set=set_,
            clear=clear,
        )
        render_result(result)
        return

    result = execute_read_only("metadata", source, engine=engine)
    render_metadata(result)


@app_commands.command(name="get-info")
def get_info(
    source: Annotated[Path, typer.Argument(help="The PDF to describe.", show_default=False)],
    engine: _EngineOption = None,
) -> None:
    """Report page count, size, encryption and metadata.

    Read-only: it never writes a file, so it takes no `-o`. An encrypted file is
    reported rather than refused — "is this locked?" is exactly the question
    this answers.
    """
    from docmax.cli.execution import execute_read_only
    from docmax.cli.render import render_info

    render_info(execute_read_only("get-info", source, engine=engine))


__all__ = ["app_commands"]
