"""The M9 commands: ``pipeline``, ``batch`` and ``watch``.

Three commands over one runner package. Everything that decides *what happens*
lives in ``docmax.runners``; this module turns argv into a call and a result into
something to look at, which is the CLI's whole job.

Kept out of ``commands.py`` because that file is the nineteen per-tool commands
and these are not tools — they compose tools. Registered into the application
the same way, so adding one touches this file alone.

## `--tool` and `--pipeline`

``batch`` and ``watch`` each need to know *what to run*, and they take it one of
two ways: ``--tool NAME`` runs that tool with its declared defaults, and
``--pipeline FILE`` runs the chain in that file. There is no third way to pass a
tool parameter to a batch — no ``--set key=value`` mini-language — because the
pipeline file already is that mechanism and a second one would need its own
quoting rules, its own type coercion and its own error messages. See ADR 0025.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from docmax.cli import commands
from docmax.core.models import Engine

if TYPE_CHECKING:
    from docmax.runners.batch import BatchReport, ItemOutcome
    from docmax.runners.pipeline import Pipeline

workflow_commands = typer.Typer()

_OutputDirOption = Annotated[
    Path,
    typer.Option(
        "--output-dir",
        help="Directory to write results into. Must exist, and hold none of the inputs.",
        show_default=False,
    ),
]
_ToolOption = Annotated[
    str | None,
    typer.Option("--tool", help="Run one tool with its default parameters."),
]
_PipelineOption = Annotated[
    Path | None,
    typer.Option("--pipeline", help="Run the chain of stages in this TOML file."),
]
_ForceOption = Annotated[
    bool,
    typer.Option("--force", "-f", help="Overwrite outputs that already exist."),
]
_EngineOption = Annotated[
    Engine | None,
    typer.Option("--engine", help="Force an engine. Default: decide from availability."),
]


@workflow_commands.command()
def pipeline(
    source: Annotated[
        Path,
        typer.Argument(help="The document to run the pipeline over.", show_default=False),
    ],
    pipeline_file: Annotated[
        Path,
        typer.Option(
            "--pipeline",
            "-p",
            help="TOML file describing the stages to run, in order.",
            show_default=False,
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Where to write the result.", show_default=False),
    ],
    force: _ForceOption = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Say which engines would run, and write nothing."),
    ] = False,
    json_out: commands.JsonOption = False,
) -> None:
    """Run several operations over one document, in order.

    Every stage goes through the same router, the same engines and the same
    validators as the equivalent single command. What a pipeline adds is that
    the intermediate documents never touch your directory: they live in one
    temporary directory, and only the final stage writes `-o`.

    A failure or a Ctrl-C at any stage therefore leaves the destination exactly
    as it was. See ADR 0024.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import EXIT_CANCELLED, EXIT_FAILURE, build_router, interruptible
    from docmax.cli.progress import ConsoleProgress
    from docmax.cli.render import console, render_error, render_result
    from docmax.core.cancellation import CancellationToken
    from docmax.core.errors import CancelledError, DocMaxError
    from docmax.core.models import DocumentRef
    from docmax.runners.pipeline import load_pipeline, run_pipeline

    json_output.note(json_out)

    router = build_router()
    token = CancellationToken()

    with interruptible(token), ConsoleProgress(console) as progress:
        try:
            chain = load_pipeline(pipeline_file)
            document = DocumentRef.from_path(source)
            target = router.target_for(
                chain.final_tool, [document], requested=str(output), force=force
            )
            result = run_pipeline(
                chain,
                source,
                target,
                router=router,
                progress=progress,
                cancellation=token,
                dry_run=dry_run,
            )
        except CancelledError as exc:
            console.print(f"[yellow]{exc.message}[/yellow] Nothing was written.")
            raise typer.Exit(EXIT_CANCELLED) from exc
        except DocMaxError as exc:
            render_error(exc)
            raise typer.Exit(EXIT_FAILURE) from exc

    render_result(result, dry_run=dry_run, tool="pipeline")


@workflow_commands.command()
def batch(
    inputs: Annotated[
        list[Path],
        typer.Argument(help="The documents to process.", show_default=False),
    ],
    output_dir: _OutputDirOption,
    tool: _ToolOption = None,
    pipeline_file: _PipelineOption = None,
    engine: _EngineOption = None,
    force: _ForceOption = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Say what would happen for each document, and write nothing."
        ),
    ] = False,
    json_out: commands.JsonOption = False,
) -> None:
    """Run one operation over many documents, one at a time.

    Each output is named after its input and written into `--output-dir`. A
    document that fails is reported and the rest carry on — one corrupt file in
    a scanned archive does not cost you the other hundred and ninety-nine.

    Two things are refused before any work starts, because neither is
    recoverable once it has happened: two inputs whose names would collide in
    the output directory, and any output that would land on an input. See
    ADR 0025.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import EXIT_CANCELLED, EXIT_FAILURE, build_router, interruptible
    from docmax.cli.progress import ConsoleProgress
    from docmax.cli.render import console, render_error
    from docmax.core.cancellation import CancellationToken
    from docmax.core.errors import DocMaxError
    from docmax.runners.batch import run_batch

    json_output.note(json_out)

    router = build_router()
    token = CancellationToken()

    with interruptible(token), ConsoleProgress(console) as progress:
        try:
            chain = _resolve_pipeline(tool=tool, pipeline_file=pipeline_file, engine=engine)
            report = run_batch(
                chain,
                inputs,
                output_dir,
                router=router,
                progress=progress,
                cancellation=token,
                force=force,
                dry_run=dry_run,
            )
        except DocMaxError as exc:
            # The whole-batch failures: a bad pipeline, a missing output
            # directory, a colliding plan. A *document* failing never lands here.
            render_error(exc)
            raise typer.Exit(EXIT_FAILURE) from exc

    _render_report(report, command="batch", pipeline=chain)

    if report.cancelled:
        raise typer.Exit(EXIT_CANCELLED)
    if report.failed:
        raise typer.Exit(EXIT_FAILURE)


@workflow_commands.command()
def watch(
    folder: Annotated[
        Path,
        typer.Argument(help="The folder to watch for new documents.", show_default=False),
    ],
    output_dir: _OutputDirOption,
    tool: _ToolOption = None,
    pipeline_file: _PipelineOption = None,
    pattern: Annotated[
        list[str] | None,
        typer.Option("--pattern", help="Filename patterns to match. Default: *.pdf"),
    ] = None,
    interval: Annotated[
        float,
        typer.Option("--interval", help="Seconds between listings."),
    ] = 1.0,
    engine: _EngineOption = None,
    force: _ForceOption = False,
    json_out: commands.JsonOption = False,
) -> None:
    """Process documents as they appear in a folder, until you stop it.

    Runs until Ctrl-C. A file is picked up once it has stopped changing — its
    size and modification time must match across two listings — so a document
    still being copied in is left alone until it is whole.

    `--output-dir` may not be inside the watched folder, and the watched folder
    may not be inside it. That rule is the whole reason this is not v2's
    watcher, which wrote beside its own input and then ate it. See ADR 0026.

    Under `--json`, one object is written when the watch ends, summarising
    everything it processed.
    """
    from docmax.cli import json_output
    from docmax.cli.execution import EXIT_CANCELLED, EXIT_FAILURE, build_router, interruptible
    from docmax.cli.progress import ConsoleProgress
    from docmax.cli.render import console, render_error
    from docmax.core.cancellation import CancellationToken
    from docmax.core.errors import DocMaxError
    from docmax.runners.watch import WatchOptions, watch_folder

    json_output.note(json_out)

    router = build_router()
    token = CancellationToken()

    def announce(outcome: ItemOutcome) -> None:
        # Printed as it happens rather than at the end: a watch has no end until
        # the user provides one. Suppressed under --json, where stdout is
        # reserved for the single object written at shutdown.
        if not json_output.enabled():
            _print_outcome(outcome)

    with interruptible(token), ConsoleProgress(console) as progress:
        try:
            chain = _resolve_pipeline(tool=tool, pipeline_file=pipeline_file, engine=engine)
            options = WatchOptions(
                folder=folder,
                output_dir=output_dir,
                patterns=tuple(pattern) if pattern else ("*.pdf",),
                interval=interval,
            )
            if not json_output.enabled():
                console.print(
                    f"[cyan]Watching[/cyan] {options.folder} "
                    f"({', '.join(options.patterns)}) — Ctrl-C to stop."
                )
            report = watch_folder(
                chain,
                options,
                router=router,
                progress=progress,
                cancellation=token,
                force=force,
                on_outcome=announce,
            )
        except DocMaxError as exc:
            render_error(exc)
            raise typer.Exit(EXIT_FAILURE) from exc

    _render_report(report, command="watch", pipeline=chain, already_printed=True)
    raise typer.Exit(EXIT_CANCELLED if report.cancelled else 0)


# -- shared -----------------------------------------------------------------


def _resolve_pipeline(
    *,
    tool: str | None,
    pipeline_file: Path | None,
    engine: Engine | None,
) -> Pipeline:
    """Turn ``--tool`` or ``--pipeline`` into the one thing the runners execute.

    Exactly one of the two is required. ``--engine`` belongs with ``--tool``
    only: a pipeline file says which engine each stage wants, and a flag that
    silently overrode every stage would make the file's own answer a lie.
    """
    from docmax.core.errors import InvalidParameterError
    from docmax.runners.pipeline import load_pipeline, single_stage

    if tool and pipeline_file:
        raise InvalidParameterError(
            "Pass either --tool or --pipeline, not both.",
            remedy="--tool runs one operation; --pipeline runs a chain of them.",
        )
    if not tool and not pipeline_file:
        raise InvalidParameterError(
            "Nothing to run: pass --tool or --pipeline.",
            remedy="For example: --tool compress, or --pipeline clean.toml.",
        )

    if pipeline_file is not None:
        if engine is not None:
            raise InvalidParameterError(
                "--engine cannot be combined with --pipeline.",
                remedy="Set 'engine' on the stage that needs it, inside the pipeline file.",
            )
        return load_pipeline(pipeline_file)

    assert tool is not None
    return single_stage(tool, engine=engine)


def _print_outcome(outcome: ItemOutcome) -> None:
    from docmax.cli.render import console, out

    if outcome.ok:
        out.print(f"[green]Wrote[/green] {outcome.destination}", soft_wrap=True)
    else:
        error = outcome.error
        assert error is not None
        console.print(
            f"[red]Failed[/red] {outcome.source.name} — [dim]{error.code.value}[/dim] {error.message}",
            soft_wrap=True,
        )


def _render_report(
    report: BatchReport,
    *,
    command: str,
    pipeline: Pipeline,
    already_printed: bool = False,
) -> None:
    """One JSON object, or a per-document listing and a summary line."""
    from docmax.cli import json_output
    from docmax.cli.render import _emit, console, out

    if json_output.enabled():
        _emit(
            json_output.many(
                {
                    "command": command,
                    "pipeline": pipeline.label,
                    "stages": [stage.tool for stage in pipeline.stages],
                    "cancelled": report.cancelled,
                    "items": [_item_payload(item) for item in report.outcomes],
                    "summary": {
                        "total": len(report.outcomes),
                        "succeeded": len(report.succeeded),
                        "failed": len(report.failed),
                    },
                },
                ok=not report.failed,
            )
        )
        return

    if not already_printed:
        for outcome in report.outcomes:
            _print_outcome(outcome)

    if not report.outcomes:
        console.print("[dim]No documents were processed.[/dim]")
        return

    summary = f"{len(report.succeeded)} succeeded, {len(report.failed)} failed"
    if report.cancelled and command == "batch":
        summary += " — stopped early"
    out.print(f"  [dim]{summary}[/dim]")


def _item_payload(outcome: ItemOutcome) -> dict[str, object]:
    """One document's result, using the same error envelope a single run uses."""
    payload: dict[str, object] = {
        "source": str(outcome.source),
        "ok": outcome.ok,
        "destination": str(outcome.destination) if outcome.destination else None,
    }
    if outcome.error is not None:
        payload["error"] = outcome.error.to_dict()
    if outcome.result is not None:
        payload["engine"] = outcome.result.engine_used.value
        payload["duration_ms"] = outcome.result.duration_ms
    return payload


__all__ = ["workflow_commands"]
