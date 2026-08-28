"""One operation over many documents, serially, isolating every failure.

Batch mirrors each input's stem into ``--output-dir`` and runs the same pipeline
over each — see ``docs/adr/0025-batch-mirrors-names-into-an-output-directory.md``.
There is no second execution path: a bare tool is a one-stage pipeline, so
parameters are passed the one way a pipeline passes them.

Three properties are the whole point of this module, and each answers a defect
v2 shipped:

* **One failure does not end the run.** v2 called ``sys.exit`` from inside an
  operation, and because ``SystemExit`` is not an ``Exception`` every
  ``except Exception`` in its batch runner missed it — one missing dependency
  killed a 200-file run. Here a failed item is recorded and the loop continues.
* **The typed error survives.** v2 flattened failures to ``(path, str(exc))``,
  which made "retry only the ones that timed out" unwritable. :class:`ItemOutcome`
  keeps the exception itself, and :meth:`BatchReport.as_exception_group` hands
  them over as the ``ExceptionGroup`` ADR 0001 raised the Python floor for.
* **No output can land on an input.** Checked across the whole batch before any
  work starts, because ``OutputTarget`` can only see the inputs of its own call.

Serial, deliberately. ADR 0025 records why, and what it would take to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from docmax.core.cancellation import NEVER_CANCELLED
from docmax.core.errors import CancelledError, DocMaxError, InvalidParameterError
from docmax.core.models import DocumentRef, OutputTarget
from docmax.core.protocols import NULL_PROGRESS
from docmax.runners._progress import LabelledProgress
from docmax.runners.pipeline import SUFFIX_FROM_PARAMS, Pipeline, run_pipeline, validate

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import ToolResult
    from docmax.core.protocols import ProgressSink
    from docmax.core.router import EngineRouter


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    """What happened to one document. Exactly one of ``result``/``error`` is set."""

    source: Path
    destination: Path | None = None
    result: ToolResult | None = None
    error: DocMaxError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class BatchReport:
    """Every outcome, in the order the documents were given."""

    outcomes: tuple[ItemOutcome, ...] = ()
    #: True when the user stopped the run; the outcomes are what finished first.
    cancelled: bool = False

    @property
    def succeeded(self) -> tuple[ItemOutcome, ...]:
        return tuple(item for item in self.outcomes if item.ok)

    @property
    def failed(self) -> tuple[ItemOutcome, ...]:
        return tuple(item for item in self.outcomes if not item.ok)

    @property
    def ok(self) -> bool:
        """True when every document that ran succeeded and none was cancelled."""
        return not self.failed and not self.cancelled

    def as_exception_group(self) -> ExceptionGroup[DocMaxError] | None:
        """The failures as one structured group, or ``None`` if there were none.

        Returned rather than raised: raising would discard the successes, and a
        batch that processed 198 of 200 documents has done most of its job. A
        caller that wants one exception asks for it here.
        """
        errors = [item.error for item in self.failed if item.error is not None]
        if not errors:
            return None
        return ExceptionGroup(f"{len(errors)} of {len(self.outcomes)} documents failed", errors)


def plan_batch(
    sources: Sequence[Path],
    output_dir: Path,
    *,
    suffix: str,
) -> tuple[tuple[Path, Path], ...]:
    """Pair each source with its destination, refusing anything contaminating.

    Both checks are fatal and both run before any work: a batch that has already
    rewritten item seven's source by the time it reaches item seven cannot be
    fixed by skipping item seven.
    """
    if not sources:
        raise InvalidParameterError(
            "No input documents were given.",
            remedy="Pass one or more files to process.",
        )

    directory = Path(output_dir).expanduser().resolve()
    if not directory.is_dir():
        raise InvalidParameterError(
            f"The output directory does not exist: {directory}",
            remedy="Create it first, or point --output-dir somewhere that exists.",
            context={"path": str(directory)},
        )

    resolved = [Path(source).expanduser().resolve() for source in sources]
    pairs = [(source, directory / f"{source.stem}{suffix}") for source in resolved]

    _refuse_duplicate_destinations(pairs)
    _refuse_destinations_that_are_inputs(pairs, inputs=set(resolved))
    return tuple(pairs)


def _refuse_duplicate_destinations(pairs: Sequence[tuple[Path, Path]]) -> None:
    seen: dict[Path, Path] = {}
    for source, destination in pairs:
        first = seen.get(destination)
        if first is not None:
            raise InvalidParameterError(
                f"Two inputs would both be written to {destination}: {first} and {source}",
                remedy=(
                    "Batch mirrors each input's name into --output-dir, so two inputs with "
                    "the same name collide. Run them as separate batches."
                ),
                context={"path": str(destination)},
            )
        seen[destination] = source


def _refuse_destinations_that_are_inputs(
    pairs: Sequence[tuple[Path, Path]],
    *,
    inputs: set[Path],
) -> None:
    for source, destination in pairs:
        if destination in inputs:
            raise InvalidParameterError(
                f"The output for {source.name} would overwrite an input: {destination}",
                remedy="Choose an --output-dir that holds none of the input documents.",
                context={"path": str(destination)},
            )


def run_batch(
    pipeline: Pipeline,
    sources: Sequence[Path],
    output_dir: Path,
    *,
    router: EngineRouter,
    progress: ProgressSink = NULL_PROGRESS,
    cancellation: CancellationToken = NEVER_CANCELLED,
    force: bool = False,
    dry_run: bool = False,
    on_outcome: Callable[[ItemOutcome], None] | None = None,
) -> BatchReport:
    """Run ``pipeline`` over every source, one at a time.

    Never raises for a failed document — that is what the report is for. It does
    raise for the conditions that make the *whole* batch wrong: an invalid
    pipeline, a missing output directory, or a destination that collides with
    another item's name or with any input.
    """
    validate(pipeline, router)
    refuse_unnameable_output(pipeline)

    suffix = router.lookup(pipeline.final_tool).default_suffix
    pairs = plan_batch(sources, output_dir, suffix=suffix)

    outcomes: list[ItemOutcome] = []
    cancelled = False
    total = len(pairs)

    for index, (source, destination) in enumerate(pairs, start=1):
        if cancellation.is_cancelled:
            # Stop *scheduling*. Nothing is half-written: the item that was
            # interrupted, if any, discarded its staged file on the way out.
            cancelled = True
            break

        label = LabelledProgress(progress, f"[{index}/{total}] {source.name}")
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
                progress=label,
                cancellation=cancellation,
                dry_run=dry_run,
            )
        except CancelledError:
            cancelled = True
            break
        except DocMaxError as exc:
            # Kept whole rather than stringified, so a caller can still ask what
            # kind of failure this was. That is the v2 defect ADR 0001 names.
            outcome = ItemOutcome(source=source, error=exc)
        else:
            outcome = ItemOutcome(source=source, destination=destination, result=result)

        outcomes.append(outcome)
        if on_outcome is not None:
            on_outcome(outcome)

    return BatchReport(outcomes=tuple(outcomes), cancelled=cancelled)


def refuse_unnameable_output(pipeline: Pipeline) -> None:
    """Refuse a batch whose output extension cannot be known from the registry.

    ``convert`` decides its extension from ``--to``, and ``ToolSpec`` cannot say
    so — the third of the three seams
    ``docs/planning/current-status.md`` records. Mirroring the input's stem onto
    ``default_suffix`` would produce two hundred files called ``.pdf`` that are
    not PDFs, which is worse than refusing.
    """
    tool = pipeline.final_tool
    if tool in SUFFIX_FROM_PARAMS:
        raise InvalidParameterError(
            f"Batch cannot name the outputs of {tool!r}: "
            "its file extension depends on a parameter rather than on the tool.",
            remedy=f"Run {tool!r} one document at a time with an explicit -o.",
            context={"tool": tool},
        )


__all__ = [
    "BatchReport",
    "ItemOutcome",
    "plan_batch",
    "refuse_unnameable_output",
    "run_batch",
]
