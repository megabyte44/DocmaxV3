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

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from docmax.core.models import Engine
from docmax.tools import _formats, _permissions, _position
from docmax.tools.protect import tool as protect_spec
from docmax.tools.resize import tool as resize_spec

if TYPE_CHECKING:
    from collections.abc import Iterator

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
#: The two ADR 0005 pickers spell this the same way, and it means the same
#: thing in both: choose the parameter visually instead of typing it, then
#: proceed down the identical path.
_InteractiveOption = Annotated[
    bool,
    typer.Option("--interactive", help="Choose the value visually, in a browser."),
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
#: `to-images` writes only what pdftoppm produces; `convert-image` reads and
#: writes every declared raster format, so the two lists genuinely differ.
_CONVERTIBLE_IMAGE_FORMATS = ", ".join(_formats.readable_image_names())
#: `resize`'s fit modes, listed by the spec that declares them.
_FIT_MODES = ", ".join(resize_spec.FIT_MODES)


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
        str | None,
        typer.Option("--order", help="The new order, e.g. 3,1,2.", show_default=False),
    ] = None,
    interactive: _InteractiveOption = False,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Reorder pages into a given sequence.

    The order must list every page exactly once. A reorder that quietly dropped
    or duplicated a page would be discovered far too late.

    `--interactive` opens the page list in your browser, you drag them into the
    order you want, and the value fills `--order`. The picker returns a
    parameter and never touches the document. See ADR 0005.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    order = _resolve_order(source, order=order, interactive=interactive)

    result = execute(
        "reorder", [source], output, engine=engine, force=force, dry_run=dry_run, order=order
    )
    render_result(result, dry_run=dry_run, tool="reorder")


def _resolve_order(source: Path, *, order: str | None, interactive: bool) -> str:
    """The page order, dragged in a browser when asked for and typed otherwise.

    `--order` stays required in every non-interactive invocation, so no existing
    command line changes meaning: what used to be a missing-option error from
    Typer is now the same refusal from here, with the picker named as the other
    way to supply it.
    """
    from docmax.cli.interactive import require_interactive_is_possible

    if not interactive:
        if order is None or not order.strip():
            raise typer.BadParameter(
                "Pass --order 3,1,2, or --interactive to arrange the pages visually.",
                param_hint="--order",
            )
        return order

    if order is not None:
        raise typer.BadParameter("Pass --order or --interactive, not both.", param_hint="--order")

    with _parameter_boundary():
        require_interactive_is_possible("--interactive")

        from docmax.pickers import pick_order

        return pick_order(source, announce=_announce)


def _announce(url: str) -> None:
    """Put the picker's URL on stderr, where the CLI's diagnostics live.

    stderr rather than stdout because a picker is a step on the way to a result,
    not the result — and because `--json` reserves stdout entirely. (`--json`
    refuses `--interactive` outright; this keeps the streams honest anyway.)
    """
    from docmax.cli.render import console

    console.print(f"[cyan]Opening a picker:[/cyan] {url}")
    console.print("[dim]  Close the tab or press Ctrl-C to cancel.[/dim]")


@contextmanager
def _parameter_boundary() -> Iterator[None]:
    """The error boundary for work that happens *before* ``execute`` is reached.

    ``cli/execution.py`` turns every typed error into a rendered panel and an
    exit code, but it only covers the run itself. Parsing `--box` and opening a
    picker both happen earlier, and both raise the same typed errors — a
    malformed box, an unreadable document, a picker timeout. Without this they
    would escape as tracebacks, which is the one thing the whole error contract
    exists to prevent.

    `PickerCancelledError` is deliberately not a `DocMaxError` — closing the tab is a
    decision, not a failure, and `cli/execution.py` already treats the
    equivalent Ctrl-C that way: a plain line and exit 130, because nothing was
    written and nothing needs explaining.
    """
    from docmax.cli.execution import EXIT_CANCELLED, EXIT_FAILURE
    from docmax.cli.render import console, render_error
    from docmax.core.errors import DocMaxError
    from docmax.pickers import PickerCancelledError

    try:
        yield
    except PickerCancelledError as exc:
        console.print("[yellow]Picker cancelled.[/yellow] Nothing was written.")
        raise typer.Exit(EXIT_CANCELLED) from exc
    except KeyboardInterrupt as exc:
        console.print("\n[yellow]Picker cancelled.[/yellow] Nothing was written.")
        raise typer.Exit(EXIT_CANCELLED) from exc
    except DocMaxError as exc:
        # The same panel and the same exit code the run itself would produce,
        # including the `--json` envelope: `render_error` reads the switch, so a
        # script gets one parseable shape whether the failure was the box it
        # typed or the document it named.
        render_error(exc)
        raise typer.Exit(EXIT_FAILURE) from exc


@app_commands.command()
def crop(
    source: Annotated[Path, typer.Argument(help="The PDF to crop.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    box: Annotated[
        str | None,
        typer.Option(
            "--box",
            help=(
                "The rectangle to keep, as x,y,width,height in points, "
                "measured from the bottom-left of the page."
            ),
            show_default=False,
        ),
    ] = None,
    interactive: _InteractiveOption = False,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Trim every page to a rectangle.

    Coordinates are in points — 72 to the inch — and the origin is the
    **bottom-left** of the page, which is the PDF coordinate system's own.

    `--interactive` opens a page in your browser, you drag a box on it, and the
    value fills `--box`. Everything after that is identical to typing the
    numbers yourself: the picker returns a parameter and never touches the
    document. See ADR 0005.

    Cropping moves the page boundary; it does not delete the marks outside it.
    Use `sanitize` when the point is to remove content rather than to reframe
    it.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    box = _resolve_box(source, box=box, interactive=interactive)

    result = execute("crop", [source], output, engine=engine, force=force, dry_run=dry_run, box=box)
    render_result(result, dry_run=dry_run, tool="crop")


def _resolve_box(source: Path, *, box: str | None, interactive: bool) -> str:
    """The box to crop to, drawn in a browser when asked for and typed otherwise.

    The picker is reached only through this function, and it returns a string
    that is then indistinguishable from one the user typed — which is what makes
    ADR 0005's "the picker fills --box, then proceeds identically" true rather
    than merely intended.
    """
    from docmax.cli.interactive import require_interactive_is_possible
    from docmax.tools import _box

    if not interactive:
        # Parsed here so a malformed box is reported before any router,
        # document or temp file is involved. The strategy parses it again;
        # both go through the same module and cannot disagree.
        with _parameter_boundary():
            return _box.parse(box).as_spec()

    if box is not None:
        raise typer.BadParameter("Pass --box or --interactive, not both.", param_hint="--box")

    with _parameter_boundary():
        require_interactive_is_possible("--interactive")

        from docmax.pickers import pick_box

        return pick_box(source, announce=_announce).as_spec()


@app_commands.command()
def ocr(
    source: Annotated[
        Path, typer.Argument(help="The scanned PDF to recognise.", show_default=False)
    ],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    lang: Annotated[
        str,
        typer.Option("--lang", help="Language code, e.g. eng or deu. Combine with +."),
    ] = "eng",
    dpi: Annotated[
        int,
        typer.Option(
            "--dpi", help="Rasterisation resolution. Higher is slower and usually better."
        ),
    ] = 300,
    deskew: Annotated[
        bool,
        typer.Option("--deskew/--no-deskew", help="Straighten pages before recognition."),
    ] = True,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Add a searchable text layer to a scanned document.

    The output has the same pages in the same order, with invisible text over
    the ones that needed recognising.

    **Pages that already carry real text are copied through untouched.** A
    scanned contract behind a generated cover page is the common case, and
    re-recognising the cover would replace its real text with a picture of it.
    The result reports which pages were skipped and why.

    A page that *is* recognised comes back as an image at `--dpi` plus its text
    layer — that is what OCR of a scan means, since the page was already an
    image.

    `--lang` takes Tesseract's codes and its `+` syntax: `--lang eng+hin`. A
    pack that is not installed is refused by name, with the installed ones
    listed.

    Needs Tesseract and Poppler locally; `--engine cloud` needs neither.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "ocr",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        lang=lang,
        dpi=dpi,
        deskew=deskew,
    )
    render_result(result, dry_run=dry_run, tool="ocr")


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


@app_commands.command(name="remove-bg")
def remove_bg(
    source: Annotated[Path, typer.Argument(help="The image to process.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    model: Annotated[
        str, typer.Option("--model", help="ONNX model: u2net, u2netp, or isnet-general-use.")
    ] = "u2net",
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Remove the background from an image, leaving a transparent PNG.

    Outputs always use PNG to preserve transparency. Requires rembg — run
    `docmax doctor` to check, or install with `pip install "DocmaxV3[remove-bg]"`.
    The ONNX model is downloaded on first use to ~/.u2net/.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "remove-bg",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        model=model,
    )
    render_result(result, dry_run=dry_run, tool="remove-bg")


@app_commands.command(name="compress-image")
def compress_image(
    source: Annotated[Path, typer.Argument(help="The image to compress.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    quality: Annotated[
        int, typer.Option("--quality", help="JPEG/WEBP quality 1-95 (PNG: lossless).")
    ] = 80,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Shrink an image file while preserving its format.

    Compresses JPEG/WEBP using the quality parameter, PNG using lossless
    optimization. Format is never changed — use `convert` to change formats.
    Requires Pillow — install with `pip install "DocmaxV3[images]"`.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "compress-image",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        quality=quality,
    )
    render_result(result, dry_run=dry_run, tool="compress-image")


@app_commands.command()
def resize(
    source: Annotated[Path, typer.Argument(help="The image to resize.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    width: Annotated[
        int | None,
        typer.Option(
            "--width",
            help="Target width in pixels. With --height omitted, the height follows the image.",
            show_default=False,
        ),
    ] = None,
    height: Annotated[
        int | None,
        typer.Option(
            "--height",
            help="Target height in pixels. With --width omitted, the width follows the image.",
            show_default=False,
        ),
    ] = None,
    scale: Annotated[
        float | None,
        typer.Option(
            "--scale",
            help="Percentage of the original — 50 halves it, 200 doubles it.",
            show_default=False,
        ),
    ] = None,
    fit: Annotated[
        str,
        typer.Option(
            "--fit",
            help=f"How to fit when both dimensions are given: {_FIT_MODES}.",
        ),
    ] = "cover",
    quality: Annotated[
        int, typer.Option("--quality", help="JPEG/WEBP quality 1-95 (PNG: lossless).")
    ] = 80,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Shrink or expand an image.

    Three ways to say how big, and any one is enough:

        --scale 50              half the original, proportions kept
        --width 800             height follows the image's own proportions
        --width 800 --height 600    an exact box, with --fit deciding the rest

    `--fit` only matters for that last form: cover (crop to fill), contain
    (pad to fit), fill (same as cover), stretch (ignore proportions). The
    other two already keep the proportions, so there is nothing to decide.

    Quality applies to JPEG/WEBP; PNG is lossless and ignores it.
    Requires Pillow — install with `pip install "DocmaxV3[images]"`.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "resize",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        width=width,
        height=height,
        scale=scale,
        fit=fit,
        quality=quality,
    )
    render_result(result, dry_run=dry_run, tool="resize")


@app_commands.command(name="convert-image")
def convert_image(
    source: Annotated[Path, typer.Argument(help="The image to convert.", show_default=False)],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the result.", show_default=False)
    ],
    to: Annotated[
        str | None,
        typer.Option(
            "--to",
            help=f"The format to convert to: {_CONVERTIBLE_IMAGE_FORMATS}. Default: from the -o extension.",
            show_default=False,
        ),
    ] = None,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Convert an image to another format.

    Supports PNG, JPEG, TIFF, BMP, and GIF. Name the format with `--to`, or
    leave it out and the extension of `-o` decides. Passing both is allowed;
    passing two that disagree is refused rather than resolved, since either
    answer would write a file whose name misdescribes its contents.

    To also shrink the file, pipe the result through `compress-image`.
    Requires Pillow — install with `pip install "DocmaxV3[images]"`.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "convert-image",
        [source],
        output,
        engine=engine,
        force=force,
        dry_run=dry_run,
        to=to,
    )
    render_result(result, dry_run=dry_run, tool="convert-image")


@app_commands.command(name="watermark-image")
def watermark_image(
    source: Annotated[Path, typer.Argument(help="The image to watermark.", show_default=False)],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Where to write the result.", show_default=False),
    ],
    text: Annotated[
        str,
        typer.Option("--text", help="The text to draw on the image.", show_default=False),
    ],
    position: Annotated[
        str,
        typer.Option(
            "--position",
            help=f"Which of the nine cells: {_POSITIONS}.",
        ),
    ] = "center",
    size: Annotated[float, typer.Option("--size", help="Font size in points.")] = 48.0,
    opacity: Annotated[
        float,
        typer.Option("--opacity", help="Opacity from 0 (invisible) to 1 (solid)."),
    ] = 0.15,
    angle: Annotated[
        float,
        typer.Option("--angle", help="Rotation angle in degrees clockwise."),
    ] = 45.0,
    force: _ForceOption = False,
    engine: _EngineOption = None,
    dry_run: _DryRunOption = False,
    json_out: JsonOption = False,
) -> None:
    """Draw semi-transparent text on an image.

    Watermarks the image with text at the specified position, size, opacity, and
    angle. The watermark is a composite overlay that sits on top of the image
    content. Requires Pillow — install with `pip install "DocmaxV3[images]"`.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import execute
    from docmax.cli.render import render_result

    json_output.note(json_out)

    result = execute(
        "watermark-image",
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
    )
    render_result(result, dry_run=dry_run, tool="watermark-image")


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

    `-o` names the destination outright rather than defaulting to `Path | None`
    with a manual check, because there is nothing conditional about it: the
    real extension depends on `--to`, so no path is ever safe to guess. That is
    `ToolSpec.output_required` (ADR 0033) for this tool, expressed the plain
    way a static fact is — a required option, not a runtime branch.
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
    from docmax.core.registry import get_tool

    json_output.note(json_out)

    # *Whether* `-o` is required depends on `--set`/`--clear`, which `ToolSpec`
    # has no way to see — that half stays a per-tool branch here, by
    # necessity. But *that no destination is ever implied* for a write is
    # unconditional, and reading it off `get_tool("metadata").output_required`
    # rather than assuming it inline is what keeps this check from silently
    # drifting from the fact `tui/app.py` reads off the same spec. ADR 0033.
    writing = bool(set_) or clear
    if writing and output is None and get_tool("metadata").output_required:
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
