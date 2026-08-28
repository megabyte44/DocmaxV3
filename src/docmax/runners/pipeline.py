"""Several operations over one document, composed without reimplementing any.

A pipeline is a TOML file naming stages; each stage names a tool the registry
already knows and parameters that tool already declares. Nothing here knows what
``ocr`` or ``compress`` do — every stage goes through :class:`EngineRouter`,
exactly as a single CLI command does. See
``docs/adr/0024-a-pipeline-is-a-toml-file.md``.

The shape of the file::

    name = "scan-cleanup"

    [[stage]]
    tool = "ocr"
    params = { lang = "eng", dpi = 300 }

    [[stage]]
    tool = "compress"
    engine = "local"
    params = { preset = "ebook" }

``params`` is a table of its own rather than keys beside ``tool`` so that a tool
with a parameter called ``tool`` or ``engine`` stays representable. Unknown keys
are refused at load, which is the rule ``core/config.py`` already applies to
``config.toml``: a misspelled key that silently does nothing is worse than one
that is rejected.

**Only the last stage writes the user's destination.** Everything before it is
written into a single :class:`~tempfile.TemporaryDirectory` that is removed on
the way out, success or failure. That is what makes a three-stage pipeline as
atomic from the outside as a one-stage one — and what keeps v2's habit of
strewing intermediate files beside a user's documents structurally unreachable.
"""

from __future__ import annotations

import tempfile
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from docmax.core.branding import CLI_NAME
from docmax.core.cancellation import NEVER_CANCELLED
from docmax.core.errors import InvalidParameterError
from docmax.core.models import DocumentRef, Engine, OutputTarget, ToolResult
from docmax.core.protocols import NULL_PROGRESS
from docmax.runners._progress import LabelledProgress

if TYPE_CHECKING:
    from collections.abc import Mapping

    from docmax.core.cancellation import CancellationToken
    from docmax.core.protocols import ProgressSink
    from docmax.core.registry import ToolSpec
    from docmax.core.router import EngineRouter

#: Keys a pipeline file may carry at the top level.
TOP_LEVEL_KEYS = frozenset({"name", "stage"})

#: Keys one ``[[stage]]`` table may carry.
STAGE_KEYS = frozenset({"tool", "params", "engine"})

#: Tools that cannot be anything but the last stage, because the next stage
#: needs one readable file and these produce a directory (``split``,
#: ``to-images``) or produce nothing at all (``get-info``, ``permissions``).
#:
#: Maintained here rather than read from ``ToolSpec``, which cannot express
#: either property. That is the fifth appearance of the same seam — see
#: ADR 0024's closing section and ADR 0021, which answered it the same way: name
#: the exception in the consumer, hold it with a test, and do not change Core
#: for it alone.
NOT_A_MIDDLE_STAGE = frozenset({"split", "to-images", "get-info", "permissions"})

#: Tools whose output *extension* depends on a parameter rather than on the tool,
#: so nothing can derive a filename for them from the registry alone. ``convert``
#: writes what ``--to`` says; ``default_suffix`` is ``.pdf`` and is documented in
#: its own spec as unused for exactly this reason.
#:
#: They may be the last stage of a pipeline the user gave an explicit ``-o``,
#: where the name comes from the user. They may not be an intermediate stage,
#: whose name this module would have to invent, and they may not be the final
#: stage of a batch, whose names are mirrored — see ``batch.refuse_unnameable_output``.
#:
#: This is the third of the three ``ToolSpec`` seams ``current-status.md``
#: records, met here for the first time outside a single tool. See ADR 0024.
SUFFIX_FROM_PARAMS = frozenset({"convert"})

#: Prefix for the per-run scratch directory. Derived rather than written out,
#: because brand literals live in ``core/branding.py`` alone and
#: ``tests/hygiene/test_branding.py`` enforces it — the same reason
#: ``core/atomic.py`` builds its own staging prefix this way.
_SCRATCH_PREFIX = f"{CLI_NAME}-pipeline-"


@dataclass(frozen=True, slots=True)
class Stage:
    """One operation in a pipeline: a tool, its parameters, and an engine wish."""

    tool: str
    params: Mapping[str, Any] = field(default_factory=dict)
    engine: Engine | None = None


@dataclass(frozen=True, slots=True)
class Pipeline:
    """An ordered chain of stages, loaded and checked but not yet run."""

    stages: tuple[Stage, ...]
    name: str = ""
    #: Where this came from, so an error can name the file rather than the shape.
    source: Path | None = None

    @property
    def label(self) -> str:
        """What to call this in a message: its name, its file, or its shape."""
        if self.name:
            return self.name
        if self.source is not None:
            return self.source.name
        return " → ".join(stage.tool for stage in self.stages)

    @property
    def final_tool(self) -> str:
        return self.stages[-1].tool


def single_stage(
    tool: str,
    *,
    engine: Engine | None = None,
    params: Mapping[str, Any] | None = None,
) -> Pipeline:
    """The one-stage pipeline behind ``batch --tool`` and ``watch --tool``.

    Batch and watch execute a pipeline and nothing else, so running a bare tool
    over many inputs is this rather than a second execution path.
    """
    return Pipeline(stages=(Stage(tool=tool, params=dict(params or {}), engine=engine),))


def load_pipeline(path: Path) -> Pipeline:
    """Read and shape-check a pipeline file. Does not consult the registry."""
    resolved = Path(path).expanduser()
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise InvalidParameterError(
            f"Could not read the pipeline file {resolved}: {exc.strerror or exc}",
            remedy="Check the path, and that the file is readable.",
            context={"path": str(resolved)},
        ) from exc

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise InvalidParameterError(
            f"The pipeline file {resolved.name} is not valid TOML: {exc}",
            remedy="Pipelines are TOML. See docs/implementation/runners.md for the shape.",
            context={"path": str(resolved)},
        ) from exc

    return pipeline_from_mapping(data, source=resolved)


def pipeline_from_mapping(data: Mapping[str, Any], *, source: Path | None = None) -> Pipeline:
    """Turn parsed TOML into a :class:`Pipeline`, refusing anything unexpected."""
    where = f" in {source.name}" if source is not None else ""

    unknown = sorted(set(data) - TOP_LEVEL_KEYS)
    if unknown:
        raise InvalidParameterError(
            f"Unknown pipeline key{'s' if len(unknown) > 1 else ''}{where}: "
            + ", ".join(repr(key) for key in unknown),
            remedy=f"A pipeline file takes {_listing(TOP_LEVEL_KEYS)}.",
            context={"keys": unknown},
        )

    name = data.get("name", "")
    if not isinstance(name, str):
        raise InvalidParameterError(
            f"The pipeline 'name'{where} must be text.",
            remedy='For example: name = "scan-cleanup".',
        )

    raw_stages = data.get("stage")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise InvalidParameterError(
            f"A pipeline{where} needs at least one [[stage]].",
            remedy='Add a stage: [[stage]] followed by tool = "ocr".',
        )

    stages = tuple(
        _stage_from_mapping(entry, index=index, where=where)
        for index, entry in enumerate(raw_stages, start=1)
    )
    return Pipeline(stages=stages, name=name, source=source)


def _stage_from_mapping(entry: object, *, index: int, where: str) -> Stage:
    if not isinstance(entry, dict):
        raise InvalidParameterError(
            f"Stage {index}{where} is not a table.",
            remedy="Each stage is a [[stage]] table.",
        )

    unknown = sorted(set(entry) - STAGE_KEYS)
    if unknown:
        raise InvalidParameterError(
            f"Unknown key{'s' if len(unknown) > 1 else ''} in stage {index}{where}: "
            + ", ".join(repr(key) for key in unknown),
            remedy=(
                f"A stage takes {_listing(STAGE_KEYS)}. "
                "Tool parameters go inside params = {{ ... }}."
            ),
            context={"stage": index, "keys": unknown},
        )

    tool = entry.get("tool")
    if not isinstance(tool, str) or not tool:
        raise InvalidParameterError(
            f"Stage {index}{where} does not name a tool.",
            remedy='Every stage needs tool = "...".',
            context={"stage": index},
        )

    params = entry.get("params", {})
    if not isinstance(params, dict):
        raise InvalidParameterError(
            f"'params' in stage {index}{where} must be a table.",
            remedy='For example: params = { lang = "eng" }.',
            context={"stage": index},
        )

    engine = _engine_from_value(entry.get("engine"), index=index, where=where)
    return Stage(tool=tool, params=dict(params), engine=engine)


def _engine_from_value(value: object, *, index: int, where: str) -> Engine | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return Engine(value)
        except ValueError:
            pass
    raise InvalidParameterError(
        f"Stage {index}{where} asks for an unknown engine: {value!r}",
        remedy=f"Choose one of {_listing({member.value for member in Engine})}.",
        context={"stage": index, "engine": str(value)},
    )


def _listing(values: object) -> str:
    assert isinstance(values, frozenset | set)
    return ", ".join(repr(value) for value in sorted(str(item) for item in values))


# -- validation -------------------------------------------------------------


def validate(pipeline: Pipeline, router: EngineRouter) -> None:
    """Check every stage against the registry, before anything is created.

    Raises :class:`InvalidParameterError` naming the stage. This runs first so
    that a typo in stage three is reported before stage one has spent two
    minutes on OCR.
    """
    last = len(pipeline.stages)
    for index, stage in enumerate(pipeline.stages, start=1):
        # Raises InvalidParameterError with its own remedy for an unknown tool.
        spec = router.lookup(stage.tool)

        if index < last and stage.tool in NOT_A_MIDDLE_STAGE:
            raise InvalidParameterError(
                f"{stage.tool!r} cannot be stage {index} of {last}: "
                "it does not produce a single file for the next stage to read.",
                remedy=f"Move {stage.tool!r} to the end of the pipeline, or remove it.",
                context={"stage": index, "tool": stage.tool},
            )

        if index < last and stage.tool in SUFFIX_FROM_PARAMS:
            raise InvalidParameterError(
                f"{stage.tool!r} cannot be stage {index} of {last}: "
                "its output extension depends on a parameter, so the next stage's "
                "input cannot be named.",
                remedy=f"Move {stage.tool!r} to the end of the pipeline, or remove it.",
                context={"stage": index, "tool": stage.tool},
            )

        _validate_params(spec, stage, index=index)


def _validate_params(spec: ToolSpec, stage: Stage, *, index: int) -> None:
    declared = {param.name: param for param in spec.params}

    unknown = sorted(set(stage.params) - set(declared))
    if unknown:
        known = _listing(set(declared)) if declared else "no parameters"
        raise InvalidParameterError(
            f"Stage {index} ({stage.tool}) has unknown parameter"
            f"{'s' if len(unknown) > 1 else ''}: " + ", ".join(repr(key) for key in unknown),
            remedy=f"{spec.name!r} takes {known}.",
            context={"stage": index, "tool": stage.tool, "keys": unknown},
        )

    missing = sorted(
        name for name, param in declared.items() if param.required and name not in stage.params
    )
    if missing:
        raise InvalidParameterError(
            f"Stage {index} ({stage.tool}) is missing required parameter"
            f"{'s' if len(missing) > 1 else ''}: " + ", ".join(repr(key) for key in missing),
            remedy=f"Add {'them' if len(missing) > 1 else 'it'} to that stage's params table.",
            context={"stage": index, "tool": stage.tool, "keys": missing},
        )

    for name, value in stage.params.items():
        _validate_choice(declared[name], value, stage=stage, index=index)


def _validate_choice(param: Any, value: object, *, stage: Stage, index: int) -> None:
    if param.choices and str(value) not in param.choices:
        raise InvalidParameterError(
            f"Stage {index} ({stage.tool}) sets {param.name}={value!r}, which is not one of "
            + ", ".join(repr(choice) for choice in param.choices),
            remedy=f"Choose one of {', '.join(repr(c) for c in param.choices)}.",
            context={"stage": index, "tool": stage.tool, "param": param.name},
        )


# -- execution --------------------------------------------------------------


def run_pipeline(
    pipeline: Pipeline,
    source: Path,
    target: OutputTarget,
    *,
    router: EngineRouter,
    progress: ProgressSink = NULL_PROGRESS,
    cancellation: CancellationToken = NEVER_CANCELLED,
    dry_run: bool = False,
) -> ToolResult:
    """Run every stage in order, and write the destination exactly once.

    Intermediate documents live in one temporary directory that is removed on
    the way out — including the failure path, which is the case it exists for.
    The final stage receives ``target`` itself, so the destination is written by
    ``core/atomic.py`` after that stage's validators pass, and not before.
    """
    validate(pipeline, router)
    cancellation.raise_if_cancelled(operation=pipeline.label)

    if dry_run:
        return _dry_run_result(pipeline, target, router=router)

    total = len(pipeline.stages)
    with tempfile.TemporaryDirectory(prefix=_SCRATCH_PREFIX) as scratch:
        staging = Path(scratch)
        current = Path(source)
        result: ToolResult | None = None

        for index, stage in enumerate(pipeline.stages, start=1):
            cancellation.raise_if_cancelled(operation=stage.tool)

            docs = [DocumentRef.from_path(current)]
            is_last = index == total
            stage_target = (
                target
                if is_last
                else _staged_target(staging, docs, stage=stage, index=index, router=router)
            )

            result = router.run(
                stage.tool,
                docs,
                stage_target,
                requested=stage.engine,
                # A one-stage pipeline says nothing extra: the tool already
                # describes itself, and "[1/1] ocr:" in front of it is noise.
                progress=(
                    progress
                    if total == 1
                    else LabelledProgress(progress, f"[{index}/{total}] {stage.tool}:")
                ),
                cancellation=cancellation,
                **stage.params,
            )
            current = stage_target.destination

    assert result is not None  # a pipeline always has at least one stage
    return replace(
        result,
        details={
            **dict(result.details),
            "pipeline": pipeline.label,
            "stages": [stage.tool for stage in pipeline.stages],
        },
    )


def _staged_target(
    staging: Path,
    docs: list[DocumentRef],
    *,
    stage: Stage,
    index: int,
    router: EngineRouter,
) -> OutputTarget:
    """A destination for an intermediate stage, inside the temporary directory.

    Goes through ``OutputTarget.resolve`` like every other destination rather
    than being constructed directly — the checks are cheap and skipping them
    here would be one code path where they do not apply.
    """
    suffix = router.lookup(stage.tool).default_suffix
    return OutputTarget.resolve(
        inputs=docs,
        requested=staging / f"{index:02d}-{stage.tool.replace('-', '_')}{suffix}",
        default_suffix=suffix,
    )


def _dry_run_result(
    pipeline: Pipeline,
    target: OutputTarget,
    *,
    router: EngineRouter,
) -> ToolResult:
    """Resolve every stage's engine and write nothing.

    Stages are not executed, because stage two of a dry run would have no input
    to read. What a caller wants from ``--dry-run`` is which engines would run
    and where the result would land, and both are answerable without doing any
    of the work.
    """
    routings = [router.resolve(stage.tool, requested=stage.engine) for stage in pipeline.stages]
    return ToolResult(
        outputs=(),
        engine_used=routings[-1].engine,
        details={
            "dry_run": True,
            "pipeline": pipeline.label,
            "destination": str(target.destination),
            # `render_result` and the JSON envelope both read a top-level
            # "reason" for a dry run, so the chain is summarised into one here
            # as well as itemised below. Two shapes of the same fact, because
            # the single-command renderer predates pipelines and is shared.
            "reason": " → ".join(
                f"{stage.tool} ({routing.engine.value})"
                for stage, routing in zip(pipeline.stages, routings, strict=True)
            ),
            "stages": [
                {"tool": stage.tool, "engine": routing.engine.value, "reason": routing.reason}
                for stage, routing in zip(pipeline.stages, routings, strict=True)
            ],
        },
    )


__all__ = [
    "NOT_A_MIDDLE_STAGE",
    "STAGE_KEYS",
    "SUFFIX_FROM_PARAMS",
    "TOP_LEVEL_KEYS",
    "Pipeline",
    "Stage",
    "load_pipeline",
    "pipeline_from_mapping",
    "run_pipeline",
    "single_stage",
    "validate",
]
