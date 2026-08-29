"""The TUI's bridge to :class:`~docmax.core.router.EngineRouter`, and nothing more.

Every rule the CLI obeys about routing lives in the router, and this module's
whole job is to reach it without re-implementing any of it. There is no engine
resolution here, no consent logic, no output-path derivation, no validation and
no atomic write — those are the router's, ``OutputTarget``'s and the tools',
exactly as they are for ``cli/execution.py``.

## Why this is not ``cli/execution.py``

It would be, if it could be. That module raises ``typer.Exit``, installs a
``SIGINT`` handler and prints to a Rich console — three things a Textual app
must not inherit, and importing it would break the
``interfaces-are-independent`` contract in any case, since ``cli`` and ``tui``
are peers.

What is duplicated instead is four lines of *construction*:
``EngineRouter(config=load(), consent=ConsentStore(...))``. That is not routing;
it is assembling the object that does the routing, and the server assembles its
own execution context for the same reason.

## No ``textual`` import

Deliberate, and load-bearing. Progress arrives here as three callables and
cancellation as the framework-independent token, so the interesting half of a
run — that the right tool is called with the right parameters, that cancelling
stops it, that a typed error comes back typed — is testable with no terminal and
no event loop. ``app.py`` supplies callbacks that marshal onto the UI thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import Engine, ToolResult
    from docmax.core.router import EngineRouter, Routing


def build_router() -> EngineRouter:
    """A router wired to the user's real configuration and consent record.

    The consent store is scoped to the endpoint the config resolved, because a
    grant is for one service — see
    [ADR 0008](../../../docs/adr/0008-consent-record.md).
    """
    from docmax.core.config import consent_file, load
    from docmax.core.consent import ConsentStore
    from docmax.core.router import EngineRouter

    config = load()
    return EngineRouter(
        config=config,
        consent=ConsentStore(consent_file(), endpoint=config.cloud_endpoint),
    )


@dataclass(slots=True)
class CallbackProgress:
    """``ProgressSink`` as three callables, so the UI half stays out of here.

    Satisfies the protocol structurally — it declares no base class, so nothing
    here creates an import edge back into ``core``.

    Like every other sink, **it must never raise**: a failure to report progress
    is not a reason to fail an operation that is otherwise succeeding. The
    callbacks come from a Textual app being driven from a worker thread, and a
    widget that has been unmounted mid-run would otherwise take the user's
    document with it.
    """

    on_start: Callable[[str, int | None], None] | None = None
    on_advance: Callable[[int], None] | None = None
    on_finish: Callable[[], None] | None = None
    #: Kept for the tests and for a UI that wants to show a total it missed.
    description: str = ""
    total: int | None = None
    completed: int = 0

    def start(self, description: str, *, total: int | None = None) -> None:
        self.description = description
        self.total = total
        self.completed = 0
        _safely(self.on_start, description, total)

    def advance(self, amount: int = 1) -> None:
        self.completed += amount
        _safely(self.on_advance, amount)

    def finish(self) -> None:
        _safely(self.on_finish)


def _safely(callback: Callable[..., Any] | None, *args: Any) -> None:
    from contextlib import suppress

    if callback is None:
        return
    with suppress(Exception):
        callback(*args)


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Everything one run needs, gathered from the form before any work starts."""

    tool: str
    inputs: tuple[Path, ...]
    output: Path | None = None
    engine: Engine | None = None
    force: bool = False
    dry_run: bool = False
    params: dict[str, Any] = field(default_factory=dict)


def preview(request: RunRequest, *, router: EngineRouter | None = None) -> Routing:
    """Which engine *would* run, and why — without running anything.

    The router's own ``resolve``, so the badge the TUI shows and the engine that
    actually runs cannot disagree. Raises the same typed errors a run would,
    including ``ConsentRequiredError``, which is how the form can offer the
    consent modal before the user commits to a long operation.
    """
    router = router or build_router()
    return router.resolve(request.tool, requested=request.engine)


def run(
    request: RunRequest,
    *,
    router: EngineRouter | None = None,
    progress: CallbackProgress | None = None,
    cancellation: CancellationToken | None = None,
) -> ToolResult:
    """Run one tool through the router. Raises; renders nothing.

    Every failure leaves as a typed ``DocMaxError`` for the app to display —
    including ``ConsentRequiredError``, which the app turns into a modal exactly
    as ``errors.py`` has specified since M0.
    """
    from docmax.core.cancellation import NEVER_CANCELLED
    from docmax.core.models import DocumentRef
    from docmax.core.protocols import NULL_PROGRESS

    router = router or build_router()
    docs = [DocumentRef.from_path(path) for path in request.inputs]

    # Destination resolution goes through the router, which already owns it and
    # knows the tool's default extension. Resolving it here would be a second
    # implementation of the in-place and already-exists checks.
    target = router.target_for(
        request.tool,
        docs,
        requested=str(request.output) if request.output is not None else None,
        force=request.force,
    )

    return router.run(
        request.tool,
        docs,
        target,
        requested=request.engine,
        progress=progress if progress is not None else NULL_PROGRESS,
        cancellation=cancellation if cancellation is not None else NEVER_CANCELLED,
        dry_run=request.dry_run,
        **request.params,
    )


def grant_consent(tool: str, *, router: EngineRouter) -> bool:
    """Record that ``tool`` may upload to the configured endpoint.

    Returns whether anything was recorded: a router with no consent store cannot
    remember an agreement, and agreeing would be a promise nothing kept — the
    same reasoning ``cli/execution.py`` applies before its own prompt.
    """
    if router.consent is None:
        return False
    router.consent.record(tool)
    return True


__all__ = [
    "CallbackProgress",
    "RunRequest",
    "build_router",
    "grant_consent",
    "preview",
    "run",
]
