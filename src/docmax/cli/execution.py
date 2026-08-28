"""The CLI's execution boundary: build a router, run a tool, turn errors into exits.

Every command routes through :func:`execute`, so the plumbing exists once rather
than once per command. When `split` and `rotate` arrive, they add argument
parsing and nothing else.

**No routing decisions are made here.** The CLI passes what the user asked for —
possibly nothing — and the router applies the precedence ladder. Duplicating any
part of that here would mean two implementations that must agree forever, which
is the failure mode the router was written to prevent.

## Exit codes

| | |
|---|---|
| `0` | success |
| `1` | any anticipated failure — the message names it and its remedy |
| `2` | usage error, from Typer's own argument parsing |
| `130` | cancelled, by the shell's `128 + SIGINT` convention |

One code for every `DocMaxError` is deliberate. `ErrorCode` is already the
project's stable, machine-readable identifier and is documented as public API; a
parallel taxonomy of exit codes would be a second such identifier to keep in
sync with the first, and they would drift. A script that needs to distinguish
`input.corrupt` from `output.exists` wants the code, and `--json` is how it will
get one at M6.
"""

from __future__ import annotations

import signal
import sys
from typing import TYPE_CHECKING, Any

import typer

from docmax.cli.progress import ConsoleProgress
from docmax.cli.render import console, render_error
from docmax.core.branding import CLI_NAME
from docmax.core.cancellation import CancellationToken
from docmax.core.config import consent_file, load
from docmax.core.errors import CancelledError, ConsentRequiredError, DocMaxError
from docmax.core.router import EngineRouter

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from docmax.core.models import Engine, ToolResult

#: Shell convention: a process killed by signal *n* reports `128 + n`, and
#: SIGINT is 2. Scripts and CI runners already read 130 as "interrupted".
EXIT_CANCELLED = 130
EXIT_FAILURE = 1


def build_router() -> EngineRouter:
    """A router wired to the user's real configuration and consent record.

    The consent store is scoped to the endpoint the config resolved, because a
    grant is for one service — see ADR 0008. Reading config here rather than in
    each command means one place decides what the user's settings are.
    """
    from docmax.core.consent import ConsentStore

    config = load()
    return EngineRouter(
        config=config,
        consent=ConsentStore(consent_file(), endpoint=config.cloud_endpoint),
    )


def _interruptible(token: CancellationToken) -> AbstractContextManager[None]:
    """Route Ctrl-C into the existing cancellation contract.

    The first interrupt asks the operation to stop, which lets the atomic
    writers discard their staged file and leave the destination untouched — a
    far better outcome than the default, which drops a `KeyboardInterrupt`
    wherever the interpreter happens to be.

    The second restores the previous handler, so a user who wants out *now*
    still gets the ordinary behaviour rather than a program that has decided it
    knows better.

    Only installable on the main thread; anywhere else this is a no-op, which is
    correct rather than merely convenient — a library caller embedding DocMax
    must not have its signal handling rewritten underneath it.
    """
    from contextlib import contextmanager

    @contextmanager
    def _handler() -> Iterator[None]:
        try:
            previous = signal.getsignal(signal.SIGINT)
        except ValueError:  # pragma: no cover — not the main thread
            yield
            return

        def on_interrupt(signum: int, frame: Any) -> None:
            console.print("\n[yellow]Stopping…[/yellow] (interrupt again to force)")
            signal.signal(signal.SIGINT, previous)
            token.cancel()

        try:
            signal.signal(signal.SIGINT, on_interrupt)
        except ValueError:  # pragma: no cover — not the main thread
            yield
            return

        try:
            yield
        finally:
            with _suppressed_value_error():
                signal.signal(signal.SIGINT, previous)

    return _handler()


def _suppressed_value_error() -> AbstractContextManager[None]:
    from contextlib import suppress

    return suppress(ValueError)


def interruptible(token: CancellationToken) -> AbstractContextManager[None]:
    """The Ctrl-C bridge, under a public name.

    ``execute`` uses it for one document. M9's ``pipeline``, ``batch`` and
    ``watch`` commands need exactly the same thing around a much longer run, and
    a second implementation of signal handling is the last thing this CLI needs.
    """
    return _interruptible(token)


def execute(
    tool: str,
    inputs: Sequence[Path],
    output: Path,
    *,
    router: EngineRouter | None = None,
    engine: Engine | None = None,
    force: bool = False,
    dry_run: bool = False,
    **params: Any,
) -> ToolResult:
    """Run one tool and render whatever happens. Raises ``typer.Exit`` on failure.

    Takes **paths**, not resolved objects, so that opening the inputs and
    resolving the destination happen *inside* the error boundary below. An
    earlier arrangement resolved them in the command body and let
    ``OutputExistsError`` escape as a traceback — the exact failure this layer
    exists to prevent, and one that only the commonest mistakes trigger.

    Destination resolution goes through the router, which already owns it and
    knows the tool's default extension. The CLI calling ``OutputTarget.resolve``
    itself would be a second implementation of a check that must never differ.

    ``router`` is injectable so tests need neither a real config file nor a real
    consent record on the machine running them.
    """
    from docmax.core.models import DocumentRef

    router = router or build_router()
    token = CancellationToken()

    with _interruptible(token), ConsoleProgress(console) as progress:
        try:
            docs = [DocumentRef.from_path(path) for path in inputs]
            target = router.target_for(tool, docs, requested=str(output), force=force)
            try:
                return router.run(
                    tool,
                    docs,
                    target,
                    requested=engine,
                    progress=progress,
                    cancellation=token,
                    dry_run=dry_run,
                    **params,
                )
            except ConsentRequiredError as exc:
                # The one error this layer answers with a question rather than a
                # refusal. `errors.py` has said since M0 that the CLI turns this
                # into a y/n prompt and the TUI into a modal; until M6 nothing
                # did, and a user meeting it got a red panel telling them to
                # agree with no way to.
                _agree_or_exit(exc, router, progress)
                return router.run(
                    tool,
                    docs,
                    target,
                    requested=engine,
                    progress=progress,
                    cancellation=token,
                    dry_run=dry_run,
                    **params,
                )
        except CancelledError as exc:
            # Not a failure: the user asked for this, and the atomic writers
            # guarantee the destination is untouched. So it gets a plain line
            # rather than the red panel an error deserves.
            console.print(f"[yellow]{exc.message}[/yellow] Nothing was written.")
            raise typer.Exit(EXIT_CANCELLED) from exc
        except DocMaxError as exc:
            render_error(exc)
            raise typer.Exit(EXIT_FAILURE) from exc


def _agree_or_exit(
    exc: ConsentRequiredError,
    router: EngineRouter,
    progress: ConsoleProgress,
) -> None:
    """Ask whether the document may be uploaded, and record the answer.

    Asked once, here, and only for a run that has already been routed to cloud —
    ``EngineRouter`` decides that, and ``offline`` has already beaten any
    ``--engine cloud`` before this point, so a policy never surfaces as a
    question.

    **Not asked when there is nobody to answer.** Under ``--json``, or when
    stdin is not a terminal, a prompt would either hang a script forever or
    corrupt its stdout. Both cases re-raise, so the caller gets the typed error
    and its remedy — which names the command that grants consent without a
    prompt.
    """
    from docmax.cli import json_output
    from docmax.cli.cloud import terms_summary

    if json_output.enabled() or not sys.stdin.isatty():
        raise exc

    if router.consent is None:
        # No store to record into. Agreeing would be a promise nothing kept.
        raise exc

    # Out of the live region: a Rich progress bar and a prompt cannot share the
    # bottom of the terminal, and the half-drawn result is unreadable.
    progress.finish()

    console.print()
    console.print(
        "[bold yellow]Upload to the cloud?[/bold yellow] "
        + terms_summary(exc.tool, router.config.cloud_endpoint)
    )
    if not typer.confirm(f"  Send {exc.tool!r} documents to this endpoint?", default=False):
        raise exc

    router.consent.record(exc.tool)
    console.print(f"  [dim]Recorded. Revoke with `{CLI_NAME} cloud revoke {exc.tool}`.[/dim]")


def execute_read_only(
    tool: str,
    source: Path,
    *,
    router: EngineRouter | None = None,
    engine: Engine | None = None,
    **params: Any,
) -> ToolResult:
    """Run a tool that writes nothing, and render whatever happens.

    ``EngineStrategy.run`` requires an ``OutputTarget`` and these tools have no
    output, so one is built directly rather than resolved. Going through
    ``router.target_for`` would check a destination that is never written — and
    would refuse the whole run with ``OutputExistsError`` if a file happened to
    sit at the derived path, which for a read-only command is a failure invented
    out of nothing.

    This is a seam in the tool contract rather than a tidy design; see
    ``tools/get_info/local.py``. The clean fix is a ``produces_output`` flag on
    ``ToolSpec``, which is a core change and has not been made here.
    """
    from docmax.core.models import DocumentRef, OutputTarget

    router = router or build_router()
    token = CancellationToken()

    with _interruptible(token), ConsoleProgress(console) as progress:
        try:
            document = DocumentRef.from_path(source)
            unused = OutputTarget(destination=document.path, force=True)
            return router.run(
                tool,
                [document],
                unused,
                requested=engine,
                progress=progress,
                cancellation=token,
                **params,
            )
        except CancelledError as exc:
            console.print(f"[yellow]{exc.message}[/yellow]")
            raise typer.Exit(EXIT_CANCELLED) from exc
        except DocMaxError as exc:
            render_error(exc)
            raise typer.Exit(EXIT_FAILURE) from exc


__all__ = [
    "EXIT_CANCELLED",
    "EXIT_FAILURE",
    "build_router",
    "execute",
    "execute_read_only",
    "interruptible",
]
