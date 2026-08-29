"""Run an operation on documents as they arrive in a folder.

v2 had this feature and it had one defect above all others: it wrote its output
into the folder it was watching, saw that output as new input, and processed it
again — the ``_preprocessed.png`` loop that
``docs/migrating-from-v2.md`` describes. Two rules here make that unreachable:
the output directory may not be inside the watched tree (nor the watched tree
inside it), and a document is keyed by content digest so nothing is processed
twice.

Polling, from the standard library — no ``watchdog``, no ``inotify``. See
``docs/adr/0026-the-watcher-polls-and-never-watches-its-own-output.md`` for why,
and for why the settle check below would be needed even with an event library:
neither ``inotify`` nor ``ReadDirectoryChangesW`` tells you a write has finished.

Nothing here prints. The caller passes ``on_outcome`` and renders what it likes,
which is what lets the CLI, and later the TUI, drive the same loop.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from docmax.core.cancellation import NEVER_CANCELLED
from docmax.core.errors import CancelledError, DocMaxError, InvalidParameterError
from docmax.core.models import DocumentRef, OutputTarget
from docmax.core.protocols import NULL_PROGRESS
from docmax.runners._progress import LabelledProgress
from docmax.runners.batch import BatchReport, ItemOutcome, refuse_unnameable_output
from docmax.runners.pipeline import Pipeline, run_pipeline, validate

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.protocols import ProgressSink
    from docmax.core.router import EngineRouter

#: How long to wait between listings, and how many identical listings a file
#: must survive before it is considered finished. Two ticks is the minimum that
#: can detect change at all; one would mean "seen once" and no stability at all.
DEFAULT_INTERVAL_SECONDS = 1.0
DEFAULT_SETTLE_TICKS = 2


@dataclass(frozen=True, slots=True)
class WatchOptions:
    """Where to watch, where to write, and how patiently."""

    folder: Path
    output_dir: Path
    patterns: tuple[str, ...] = ("*.pdf",)
    interval: float = DEFAULT_INTERVAL_SECONDS
    settle_ticks: int = DEFAULT_SETTLE_TICKS

    def __post_init__(self) -> None:
        if self.interval <= 0:
            raise InvalidParameterError(
                f"The watch interval must be positive, not {self.interval}.",
                remedy="Pass --interval with a number of seconds greater than zero.",
            )
        if self.settle_ticks < DEFAULT_SETTLE_TICKS:
            raise InvalidParameterError(
                f"A file must be seen unchanged at least twice, not {self.settle_ticks}.",
                remedy="Leave --settle-ticks at 2 or raise it.",
            )
        if not self.patterns:
            raise InvalidParameterError(
                "No filename pattern was given.",
                remedy="Pass --pattern, for example --pattern '*.pdf'.",
            )


@dataclass(slots=True)
class _Settling:
    """What the last tick saw of one path, and how many ticks have agreed."""

    fingerprint: tuple[int, int]
    ticks: int = 1


@dataclass(slots=True)
class _WatchState:
    """Everything the loop remembers. In memory, and only for this run.

    Not a journal. ADR 0025 defers persistence out of M9 rather than inventing a
    file format for it, and this is the other half of that: a restarted watcher
    forgets what it has seen, and the mirrored destinations then refuse to be
    overwritten without ``--force``. Noisy, and safe.
    """

    settling: dict[Path, _Settling] = field(default_factory=dict)
    processed: set[str] = field(default_factory=set)


def _stopped(cancellation: CancellationToken) -> bool:
    """Read the token through a call, so the loop below stays type-checkable.

    ``is_cancelled`` is a property, and mypy narrows a property exactly as it
    narrows a plain attribute: after one ``if token.is_cancelled``, every later
    read in the same scope is assumed False and the cancellation handling becomes
    "unreachable". It is emphatically reachable — another thread sets it, which
    is the entire contract of ``CancellationToken``. A function call is opaque to
    that narrowing, which is why this exists.
    """
    return cancellation.is_cancelled


def watch_folder(
    pipeline: Pipeline,
    options: WatchOptions,
    *,
    router: EngineRouter,
    progress: ProgressSink = NULL_PROGRESS,
    cancellation: CancellationToken = NEVER_CANCELLED,
    force: bool = False,
    on_outcome: Callable[[ItemOutcome], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> BatchReport:
    """Watch until cancelled, and report everything processed along the way.

    Returns rather than raises when the user stops it: a watch that ran for six
    hours and handled four hundred documents has a result worth reporting, and
    ``CancelledError`` would throw it away.

    ``sleep`` is injectable so the tests can drive the loop tick by tick without
    spending a real second on each one.
    """
    validate(pipeline, router)
    refuse_unnameable_output(pipeline)
    _refuse_overlap(options)

    suffix = router.lookup(pipeline.final_tool).default_suffix
    state = _WatchState()
    outcomes: list[ItemOutcome] = []

    while not _stopped(cancellation):
        for source in _settled(options, state):
            if _stopped(cancellation):
                break

            digest = _digest(source)
            if digest is None or digest in state.processed:
                continue

            # Recorded before the run, and kept whether or not it succeeds. A
            # document that fails is reported once rather than every tick
            # forever — see ADR 0026.
            state.processed.add(digest)

            outcome = _process(
                pipeline,
                source,
                options.output_dir / f"{source.stem}{suffix}",
                router=router,
                progress=progress,
                cancellation=cancellation,
                suffix=suffix,
                force=force,
            )
            if outcome is None:  # cancelled mid-document
                break

            outcomes.append(outcome)
            if on_outcome is not None:
                on_outcome(outcome)

        if _stopped(cancellation):
            break
        sleep(options.interval)

    return BatchReport(outcomes=tuple(outcomes), cancelled=True)


def _process(
    pipeline: Pipeline,
    source: Path,
    destination: Path,
    *,
    router: EngineRouter,
    progress: ProgressSink,
    cancellation: CancellationToken,
    suffix: str,
    force: bool,
) -> ItemOutcome | None:
    """Run one document. ``None`` means the user cancelled during it.

    Every anticipated failure is caught and returned as an outcome, because a
    watcher that exits on the first corrupt document is a watcher nobody can
    leave running.
    """
    try:
        document = DocumentRef.from_path(source)
        target = OutputTarget.resolve(
            inputs=[document],
            requested=destination,
            default_suffix=suffix,
            force=force,
        )
        result = run_pipeline(
            pipeline,
            source,
            target,
            router=router,
            progress=LabelledProgress(progress, source.name),
            cancellation=cancellation,
        )
    except CancelledError:
        return None
    except DocMaxError as exc:
        return ItemOutcome(source=source, error=exc)
    return ItemOutcome(source=source, destination=destination, result=result)


def _settled(options: WatchOptions, state: _WatchState) -> Iterator[Path]:
    """Yield the files whose size and mtime have not changed for enough ticks.

    A file being copied in changes on at least one of the two between listings,
    so it waits. This is the check that stops a 40 MB scan being read when four
    megabytes of it exist.
    """
    for path in _candidates(options):
        try:
            stat = path.stat()
        except OSError:
            # Vanished between the listing and the stat. Not an error: the file
            # was never ours, and it will reappear in a later tick if it returns.
            state.settling.pop(path, None)
            continue

        fingerprint = (stat.st_size, stat.st_mtime_ns)
        previous = state.settling.get(path)
        if previous is not None and previous.fingerprint == fingerprint:
            previous.ticks += 1
        else:
            state.settling[path] = _Settling(fingerprint=fingerprint)

        if state.settling[path].ticks >= options.settle_ticks:
            yield path


def _candidates(options: WatchOptions) -> Sequence[Path]:
    """Every file in the watched folder matching any pattern, in a stable order.

    Non-recursive: the milestone says "folder watch", and a recursive watch turns
    the containment rule into a subtree question. Additive later.
    """
    found: set[Path] = set()
    for pattern in options.patterns:
        try:
            found.update(path for path in options.folder.glob(pattern) if path.is_file())
        except OSError:
            # The folder went away underneath us. The next tick will find it
            # again, or keep finding nothing; either way this is not fatal.
            return ()
    return sorted(found)


def _digest(path: Path) -> str | None:
    """Content digest, or ``None`` if the file could not be read this tick.

    ``hashlib.file_digest`` is the fourth feature ADR 0001 raised the Python
    floor for, and this is the use it was named for — chunked hashing of a file
    that may be large, without a hand-rolled read loop.
    """
    try:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "blake2b").hexdigest()
    except OSError:
        return None


def _refuse_overlap(options: WatchOptions) -> None:
    """Refuse a watch whose output would land where it is watching, either way round.

    Stated as containment in both directions rather than as inequality, because
    ``--output-dir inbox/done`` is the mistake a user will actually make and
    plain inequality would wave it through — which is precisely how v2's watcher
    came to feed on its own output.
    """
    watched = options.folder.expanduser().resolve()
    written = options.output_dir.expanduser().resolve()

    if not watched.is_dir():
        raise InvalidParameterError(
            f"The folder to watch does not exist: {watched}",
            remedy="Point --folder at a directory that exists.",
            context={"path": str(watched)},
        )
    if not written.is_dir():
        raise InvalidParameterError(
            f"The output directory does not exist: {written}",
            remedy="Create it first, or point --output-dir somewhere that exists.",
            context={"path": str(written)},
        )

    if written == watched or written.is_relative_to(watched) or watched.is_relative_to(written):
        raise InvalidParameterError(
            f"The output directory {written} overlaps the watched folder {watched}.",
            remedy=(
                "Choose an --output-dir outside the watched folder. A watcher that writes "
                "where it watches processes its own output forever."
            ),
            context={"watched": str(watched), "output_dir": str(written)},
        )


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_SETTLE_TICKS",
    "WatchOptions",
    "watch_folder",
]
