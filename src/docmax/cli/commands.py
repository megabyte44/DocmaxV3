"""The tool commands — everything except ``merge`` and ``doctor``.

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

``get-info``, ``permissions`` and a bare ``metadata`` write nothing, but
``EngineStrategy.run`` requires an ``OutputTarget``. Those three build one
directly rather than through ``router.target_for``, because resolution would
check a destination that is never written — and would refuse the run with
``OutputExistsError`` if a file happened to sit at the derived path. See the note
in ``tools/get_info/local.py``: the clean fix is a ``produces_output`` flag on
``ToolSpec``, which is a core change and is being reported rather than made. It
was two tools at M2 and is three now, which is the point at which it stops being
a curiosity.

## Vocabularies come from the modules that own them

``--position``, ``--allow`` and ``--algorithm`` list their accepted values by
importing them from ``tools/_position.py``, ``tools/_permissions.py`` and
``tools/protect/tool.py``. Retyping the names into help text would put a second
copy of each list in the layer least likely to be updated when one changes.
Those modules import nothing but ``core``, so this costs the CLI nothing at
startup — the same property that lets the registry read every ``tool.py`` on
every ``--help``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from docmax.core.models import Engine
from docmax.tools import _formats, _permissions, _position
from docmax.tools.protect import tool as protect_spec

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
#: Also declared on the root callback, so `--json` is accepted both before and
#: after the command name. Declared once here and reused, exactly as `--force`
#: and `--engine` are: an option that means the same thing everywhere should
#: gain a behaviour in one place rather than in eighteen.
JsonOption = Annotated[
    bool,
    typer.Option("--json", help="Emit one JSON object on stdout. Diagnostics go to stderr."),
]
_PagesOption = Annotated[
    str | None,
    typer.Option("--pages", help="Which pages, e.g. 1-3,7. Default: all."),
]

#: Rendered into `--position` help so the nine names are listed by the module
#: that defines them rather than retyped per command.
_POSITIONS = ", ".join(_position.NAMES)

#: Likewise for the permission vocabulary and the encryption algorithms:
#: listed by the modules that define them rather than retyped in help text.
_PERMISSIONS = ", ".join(_permissions.NAMES)
_ALGORITHMS = ", ".join(protect_spec.ALGORITHMS)
_DEFAULT_ALGORITHM = protect_spec.DEFAULT_ALGORITHM

#: And the format vocabulary. `formats` renders the same declaration in
#: full; these two strings are only the short version for `--help`. ADR 0010.
_CONVERT_FORMATS = ", ".join(_formats.convertible_names())
_IMAGE_FORMATS = ", ".join(_formats.rasterisable_names())


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
    json_out: JsonOption = False,
) -> None:
    """Split a PDF into several files.

    The output is a **directory**, staged and swapped in as a unit — a cancelled
    run leaves the destination exactly as it was rather than holding the first
    few parts.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

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
    render_result(result, dry_run=dry_run, tool="split")


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
    json_out: JsonOption = False,
) -> None:
    """Rotate pages by a multiple of 90 degrees."""
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

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
    render_result(result, dry_run=dry_run, tool="rotate")


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
    json_out: JsonOption = False,
) -> None:
    """Keep or delete selected pages.

    ``--select`` and ``--delete`` are two ways of saying the same thing and
    cannot be combined; the tool refuses rather than inventing a precedence.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

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
    render_result(result, dry_run=dry_run, tool="pages")


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
    json_out: JsonOption = False,
) -> None:
    """Reorder pages into a given sequence.

    The order must list every page exactly once. A reorder that quietly dropped
    or duplicated a page would be discovered far too late.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "reorder", [source], output, engine=engine, force=force, dry_run=dry_run, order=order
    )
    render_result(result, dry_run=dry_run, tool="reorder")


@app_commands.command()
def sanitize(
    source: Annotated[Path, typer.Argument(help="The PDF to clean.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Remove JavaScript, embedded files and auto-actions.

    Removes document-level JavaScript and embedded files, `/OpenAction`,
    `/AcroForm`, and page-level `/AA` and `/Annots`. It does **not** inspect
    content streams and is no defence against a PDF crafted to attack a viewer's
    parser. Outlines and links are lost along with the actions.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute("sanitize", [source], output, engine=engine, force=force, dry_run=dry_run)
    render_result(result, dry_run=dry_run, tool="reorder")


@app_commands.command()
def watermark(
    source: Annotated[Path, typer.Argument(help="The PDF to mark.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    text: Annotated[str, typer.Option("--text", help="The words to draw.", show_default=False)],
    position: Annotated[
        str, typer.Option("--position", help=f"Where it sits: {_POSITIONS}.")
    ] = "center",
    size: Annotated[float, typer.Option("--size", help="Font size in points.")] = 48.0,
    opacity: Annotated[float, typer.Option("--opacity", help="0 is invisible, 1 is solid.")] = 0.15,
    angle: Annotated[
        float, typer.Option("--angle", help="Degrees anticlockwise from horizontal.")
    ] = 45.0,
    pages: _PagesOption = None,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Draw text across every page.

    Vector text in Helvetica, drawn over the existing content — nothing is
    rasterised and nothing underneath is changed. A watermark is a *mark*, not a
    protection: it sits in its own layer and any PDF editor can take it off
    again. Use `protect` for a document that must not be opened.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "watermark",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        text=text,
        position=position,
        size=size,
        opacity=opacity,
        angle=angle,
        pages=pages,
    )
    render_result(result, dry_run=dry_run, tool="watermark")


@app_commands.command()
def stamp(
    source: Annotated[Path, typer.Argument(help="The PDF to stamp.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    overlay: Annotated[
        Path,
        typer.Option("--stamp", help="The PDF whose first page is the stamp.", show_default=False),
    ],
    position: Annotated[
        str, typer.Option("--position", help=f"Where it sits: {_POSITIONS}.")
    ] = "bottom-right",
    scale: Annotated[
        float, typer.Option("--scale", help="Resize the stamp. 1 is its own size.")
    ] = 1.0,
    pages: _PagesOption = None,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Draw another PDF's first page onto every page.

    The stamp keeps whatever it is — vector artwork stays vector, embedded fonts
    stay embedded — because its content stream is transformed and merged rather
    than rendered. Only the first page of the stamp document is used.

    `--stamp` is a genuine input, not just a path: it is checked against `-o` the
    same way the source is, so a command that would overwrite the stamp while
    reading it is refused.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "stamp",
        [source, overlay],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        position=position,
        scale=scale,
        pages=pages,
    )
    render_result(result, dry_run=dry_run, tool="stamp")


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
    json_out: JsonOption = False,
) -> None:
    """Shrink a PDF with Ghostscript.

    Needs Ghostscript installed — run `docmax doctor` to check, and it will
    print the install line for your platform. The page count is verified before
    the result replaces anything, because a smaller file with fewer pages is not
    a compressed document.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "compress",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        preset=preset,
    )
    render_result(result, dry_run=dry_run, tool="compress")


@app_commands.command()
def protect(
    source: Annotated[Path, typer.Argument(help="The PDF to encrypt.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    password: Annotated[
        str,
        typer.Option("--password", help="The password needed to open it.", show_default=False),
    ],
    owner_password: Annotated[
        str | None,
        typer.Option(
            "--owner-password",
            help="The password that bypasses the permissions. Default: the same one.",
        ),
    ] = None,
    allow: Annotated[
        list[str] | None,
        typer.Option("--allow", help=f"What a reader may do: {_PERMISSIONS}, all, none."),
    ] = None,
    algorithm: Annotated[
        str, typer.Option("--algorithm", help=f"One of: {_ALGORITHMS}.")
    ] = _DEFAULT_ALGORITHM,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Encrypt a PDF with a password.

    AES-256 by default, which needs the `crypto` extra —
    `pip install "DocmaxV3[crypto]"`. The weaker RC4 algorithms work with no
    extra install and have to be asked for by name, because a tool called
    `protect` should not quietly hand you broken encryption.

    `--allow` narrows what a reader may do. Those bits are **advisory**: they
    tell a conforming reader what you would prefer, and nothing forces one to
    obey. The encryption is the boundary; the permissions are a request.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "protect",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        password=password,
        owner_password=owner_password,
        allow=allow,
        algorithm=algorithm,
    )
    render_result(result, dry_run=dry_run, tool="protect")


@app_commands.command()
def unlock(
    source: Annotated[Path, typer.Argument(help="The PDF to unlock.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    password: Annotated[
        str,
        typer.Option("--password", help="The password that opens it.", show_default=False),
    ],
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Write a copy of an encrypted PDF with the password removed.

    You have to supply a password that already opens the document — either the
    user or the owner one. This does not recover, guess or break a password, and
    it never will.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "unlock",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        password=password,
    )
    render_result(result, dry_run=dry_run, tool="unlock")


@app_commands.command()
def permissions(
    source: Annotated[Path, typer.Argument(help="The PDF to inspect.", show_default=False)],
    password: Annotated[
        str | None,
        typer.Option("--password", help="Needed if the document is encrypted."),
    ] = None,
    engine: _EngineOption = None,
    json_out: JsonOption = False,
) -> None:
    """Report what a PDF says a reader may do with it.

    Read-only: it never writes a file, so it takes no `-o`. `protect` is where
    permissions are set.

    A PDF with no encryption has no permission field at all, so everything is
    reported as allowed — that is the truth about the file rather than a
    default. And every bit here is advisory: a reader that ignores them is not
    breaking anything.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute_read_only
    from docmax.cli.render import render_permissions

    json_output.note(json_out)

    render_permissions(execute_read_only("permissions", source, engine=engine, password=password))


@app_commands.command()
def convert(
    source: Annotated[Path, typer.Argument(help="The document to convert.", show_default=False)],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Where to write the result.", show_default=False),
    ],
    to: Annotated[
        str,
        typer.Option("--to", help=f"Target format: {_CONVERT_FORMATS}.", show_default=False),
    ],
    standalone: Annotated[
        bool,
        typer.Option(
            "--standalone/--no-standalone",
            help="Produce a complete document rather than a fragment.",
        ),
    ] = True,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Convert a document to another format with Pandoc.

    **PDF is not supported in either direction.** Pandoc has no PDF reader, and
    writing PDF needs a LaTeX distribution DocMax does not install — see
    [ADR 0011](https://github.com/megabyte44/docmax/blob/main/docs/adr/0011-convert-is-pandoc-only.md).
    To turn a PDF into images use `to-images`.

    Needs Pandoc installed — run `docmax doctor` to check, and it will print the
    install line for your platform. Run `docmax formats` for the full table.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "convert",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        to=to,
        standalone=standalone,
    )
    render_result(result, dry_run=dry_run, tool="convert")


@app_commands.command(name="to-images")
def to_images(
    source: Annotated[Path, typer.Argument(help="The PDF to render.", show_default=False)],
    output: Annotated[
        Path,
        typer.Option(
            "--output", "-o", help="Directory to write the images into.", show_default=False
        ),
    ],
    image_format: Annotated[
        str, typer.Option("--format", help=f"Image format: {_IMAGE_FORMATS}.")
    ] = "png",
    dpi: Annotated[int, typer.Option("--dpi", help="Resolution in dots per inch.")] = 150,
    pages: _PagesOption = None,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Render each page of a PDF as an image.

    The output is a **directory**, staged and swapped in as a unit — a cancelled
    run leaves the destination exactly as it was rather than holding the first
    few pages.

    Needs Poppler installed — run `docmax doctor` to check. Every image is
    verified to carry a real header of its format before the directory replaces
    anything.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "to-images",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        format=image_format,
        dpi=dpi,
        pages=pages,
    )
    render_result(result, dry_run=dry_run, tool="to-images")


@app_commands.command(name="from-images")
def from_images(
    images: Annotated[
        list[Path],
        typer.Argument(
            help="Images to combine, in the order they should appear.",
            show_default=False,
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Where to write the PDF.", show_default=False),
    ],
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Combine images into a PDF, one page per image.

    Page order is argument order, and each page is exactly as large as its image
    — nothing is scaled or cropped. JPEGs are embedded without being re-encoded.

    `-o` pointing at one of the images is refused, not obeyed.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "from-images",
        images,
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
    )
    render_result(result, dry_run=dry_run, tool="from-images")


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
    json_out: JsonOption = False,
) -> None:
    """Read or set document metadata.

    With no `--set` it reads and writes nothing. With `--set`, `-o` is required:
    the result is a new document, and this tool will not edit a file in place.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute, execute_read_only
    from docmax.cli.render import render_metadata, render_result

    json_output.note(json_out)

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
        render_result(result, tool="metadata")
        return

    result = execute_read_only("metadata", source, engine=engine)
    render_metadata(result)


@app_commands.command(name="get-info")
def get_info(
    source: Annotated[Path, typer.Argument(help="The PDF to describe.", show_default=False)],
    engine: _EngineOption = None,
    json_out: JsonOption = False,
) -> None:
    """Report page count, size, encryption and metadata.

    Read-only: it never writes a file, so it takes no `-o`. An encrypted file is
    reported rather than refused — "is this locked?" is exactly the question
    this answers.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute_read_only
    from docmax.cli.render import render_info

    json_output.note(json_out)

    render_info(execute_read_only("get-info", source, engine=engine))


#: Exported because ``main.py``'s own commands -- `merge`, `doctor`,
#: `formats` -- need the same option, and two spellings of `--json` would be
#: the drift this alias exists to prevent.
__all__ = ["JsonOption", "app_commands"]
