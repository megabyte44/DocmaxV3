"""Pipelines: the file format, the validation, and the one atomic destination.

The claims under test are ADR 0024's, and two of them carry the weight:

* **Only the last stage writes the destination.** A three-stage run leaves one
  new file in the user's directory and nothing else, anywhere.
* **A failure or a cancellation leaves the destination untouched**, which is the
  property that makes composing operations safe rather than merely convenient.

Everything else here is the refusal surface: a pipeline that cannot work is
refused before it makes a temporary directory, not part-way through stage two.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from docmax.core.cancellation import CancellationToken
from docmax.core.errors import CorruptDocumentError, InvalidParameterError
from docmax.core.models import Engine, OutputTarget
from docmax.runners.pipeline import (
    NOT_A_MIDDLE_STAGE,
    SUFFIX_FROM_PARAMS,
    Pipeline,
    Stage,
    load_pipeline,
    pipeline_from_mapping,
    run_pipeline,
    single_stage,
    validate,
)
from tests.unit.m9_support import (
    ANGLE,
    CHOICE,
    REQUIRED_TO,
    RecordingProgress,
    document,
    markers,
    router_for,
    strategy_of,
    tool,
)

if TYPE_CHECKING:
    from docmax.core.router import EngineRouter


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return document(tmp_path / "in.pdf")


def target_for(tmp_path: Path, name: str = "out.pdf") -> OutputTarget:
    return OutputTarget.resolve(inputs=[], requested=tmp_path / name)


# ---------------------------------------------------------------------------
# The file format
# ---------------------------------------------------------------------------


def test_a_pipeline_file_parses_into_stages(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    path.write_text(
        """
        name = "clean"

        [[stage]]
        tool = "ocr"
        params = { lang = "eng" }

        [[stage]]
        tool = "compress"
        engine = "local"
        """,
        encoding="utf-8",
    )

    chain = load_pipeline(path)

    assert chain.name == "clean"
    assert [stage.tool for stage in chain.stages] == ["ocr", "compress"]
    assert chain.stages[0].params == {"lang": "eng"}
    assert chain.stages[1].engine is Engine.LOCAL
    assert chain.final_tool == "compress"


def test_params_live_in_their_own_table_so_a_tool_parameter_may_be_called_tool() -> None:
    """The reason for the nesting, asserted rather than merely explained."""
    chain = pipeline_from_mapping(
        {"stage": [{"tool": "widget", "params": {"tool": "hammer", "engine": "steam"}}]}
    )

    assert chain.stages[0].tool == "widget"
    assert chain.stages[0].params == {"tool": "hammer", "engine": "steam"}


def test_an_unknown_top_level_key_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="stages"):
        pipeline_from_mapping({"stages": [], "stage": [{"tool": "a"}]})


def test_an_unknown_stage_key_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="lang"):
        pipeline_from_mapping({"stage": [{"tool": "ocr", "lang": "eng"}]})


def test_a_pipeline_with_no_stages_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="at least one"):
        pipeline_from_mapping({"name": "empty"})


def test_a_stage_without_a_tool_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="does not name a tool"):
        pipeline_from_mapping({"stage": [{"params": {"lang": "eng"}}]})


def test_an_unknown_engine_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="unknown engine"):
        pipeline_from_mapping({"stage": [{"tool": "ocr", "engine": "quantum"}]})


def test_malformed_toml_is_an_error_not_a_traceback(tmp_path: Path) -> None:
    path = tmp_path / "broken.toml"
    path.write_text("[[stage]\ntool = ", encoding="utf-8")

    with pytest.raises(InvalidParameterError, match=r"not valid TOML"):
        load_pipeline(path)


def test_a_missing_pipeline_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(InvalidParameterError, match="Could not read"):
        load_pipeline(tmp_path / "absent.toml")


def test_the_error_names_the_file_it_came_from(tmp_path: Path) -> None:
    path = tmp_path / "named.toml"
    path.write_text("wrong = 1\n[[stage]]\ntool = 'a'\n", encoding="utf-8")

    with pytest.raises(InvalidParameterError, match=r"named\.toml"):
        load_pipeline(path)


# ---------------------------------------------------------------------------
# Validation, before anything runs
# ---------------------------------------------------------------------------


def test_an_unknown_tool_is_refused() -> None:
    router = router_for(tool("a"))

    with pytest.raises(InvalidParameterError, match="Unknown tool"):
        validate(single_stage("nonesuch"), router)


def test_an_unknown_parameter_is_refused_and_lists_what_the_tool_takes() -> None:
    router = router_for(tool("rotate", params=(ANGLE,)))
    chain = single_stage("rotate", params={"angle": 90})

    with pytest.raises(InvalidParameterError, match="unknown parameter") as caught:
        validate(chain, router)

    assert "'by'" in (caught.value.remedy or "")


def test_a_missing_required_parameter_is_refused() -> None:
    router = router_for(tool("convert", params=(REQUIRED_TO,)))

    with pytest.raises(InvalidParameterError, match="missing required parameter"):
        validate(single_stage("convert"), router)


def test_a_value_outside_a_declared_choice_is_refused() -> None:
    router = router_for(tool("compress", params=(CHOICE,)))
    chain = single_stage("compress", params={"preset": "lossless"})

    with pytest.raises(InvalidParameterError, match="not one of"):
        validate(chain, router)


@pytest.mark.parametrize("name", sorted(NOT_A_MIDDLE_STAGE))
def test_directory_producing_tools_are_refused_before_the_final_stage(name: str) -> None:
    """ADR 0024's frozenset, held one entry at a time.

    Each of these either produces a directory or produces nothing, so the next
    stage would have no single file to read.
    """
    router = router_for(tool(name), tool("after"))
    chain = Pipeline(stages=(Stage(tool=name), Stage(tool="after")))

    with pytest.raises(InvalidParameterError, match="cannot be stage 1 of 2"):
        validate(chain, router)


@pytest.mark.parametrize("name", sorted(NOT_A_MIDDLE_STAGE | SUFFIX_FROM_PARAMS))
def test_those_same_tools_are_allowed_as_the_last_stage(name: str) -> None:
    """The rule is about position, not about the tool being unusable."""
    params = (REQUIRED_TO,) if name in SUFFIX_FROM_PARAMS else ()
    stage_params = {"to": "html"} if name in SUFFIX_FROM_PARAMS else {}
    router = router_for(tool("before"), tool(name, params=params))
    chain = Pipeline(stages=(Stage(tool="before"), Stage(tool=name, params=stage_params)))

    validate(chain, router)  # does not raise


def test_a_parameter_dependent_extension_is_refused_mid_pipeline() -> None:
    router = router_for(tool("convert", params=(REQUIRED_TO,)), tool("after"))
    chain = Pipeline(
        stages=(Stage(tool="convert", params={"to": "html"}), Stage(tool="after")),
    )

    with pytest.raises(InvalidParameterError, match="depends on a parameter"):
        validate(chain, router)


def test_validation_happens_before_any_stage_runs(source: Path, tmp_path: Path) -> None:
    """A typo in stage two must not cost two minutes of stage one."""
    first = tool("a")
    router = router_for(first)
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="ghost")))

    with pytest.raises(InvalidParameterError):
        run_pipeline(chain, source, target_for(tmp_path), router=router)

    assert strategy_of(first).calls == []


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def test_a_single_stage_pipeline_writes_the_destination(source: Path, tmp_path: Path) -> None:
    router = router_for(tool("a"))
    target = target_for(tmp_path)

    result = run_pipeline(single_stage("a"), source, target, router=router)

    assert target.destination.exists()
    assert markers(target.destination) == ["a"]
    assert result.outputs == (target.destination,)


def test_stages_run_in_order_and_each_reads_the_last(source: Path, tmp_path: Path) -> None:
    router = router_for(tool("a"), tool("b"), tool("c"))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b"), Stage(tool="c")))
    target = target_for(tmp_path)

    run_pipeline(chain, source, target, router=router)

    assert markers(target.destination) == ["a", "b", "c"]


def test_only_the_last_stage_writes_the_destination_directory(source: Path, tmp_path: Path) -> None:
    """Three stages, one new file. The intermediates were never here."""
    outputs = tmp_path / "out"
    outputs.mkdir()
    router = router_for(tool("a"), tool("b"), tool("c"))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b"), Stage(tool="c")))

    run_pipeline(chain, source, target_for(outputs), router=router)

    assert [path.name for path in outputs.iterdir()] == ["out.pdf"]


def test_intermediate_stages_write_inside_a_temporary_directory(
    source: Path, tmp_path: Path
) -> None:
    seen: list[Path] = []
    first = tool("a", hook=lambda target: seen.append(target.destination))
    router = router_for(first, tool("b"))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b")))

    run_pipeline(chain, source, target_for(tmp_path), router=router)

    staged = seen[0]
    assert staged.is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert not staged.exists(), "the temporary directory outlived the run"


def test_the_temporary_directory_is_removed_after_a_failure(source: Path, tmp_path: Path) -> None:
    seen: list[Path] = []
    first = tool("a", hook=lambda target: seen.append(target.destination))
    router = router_for(first, tool("b", raises=CorruptDocumentError("no")))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b")))

    with pytest.raises(CorruptDocumentError):
        run_pipeline(chain, source, target_for(tmp_path), router=router)

    assert not seen[0].parent.exists()


def test_a_failed_middle_stage_leaves_no_destination(source: Path, tmp_path: Path) -> None:
    router = router_for(tool("a"), tool("b", raises=CorruptDocumentError("bad")), tool("c"))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b"), Stage(tool="c")))
    target = target_for(tmp_path)

    with pytest.raises(CorruptDocumentError):
        run_pipeline(chain, source, target, router=router)

    assert not target.destination.exists()


def test_a_failed_last_stage_leaves_no_destination(source: Path, tmp_path: Path) -> None:
    router = router_for(tool("a"), tool("b", raises=CorruptDocumentError("bad")))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b")))
    target = target_for(tmp_path)

    with pytest.raises(CorruptDocumentError):
        run_pipeline(chain, source, target, router=router)

    assert not target.destination.exists()


def test_a_later_stage_does_not_run_after_an_earlier_one_fails(
    source: Path, tmp_path: Path
) -> None:
    third = tool("c")
    router = router_for(tool("a"), tool("b", raises=CorruptDocumentError("bad")), third)
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b"), Stage(tool="c")))

    with pytest.raises(CorruptDocumentError):
        run_pipeline(chain, source, target_for(tmp_path), router=router)

    assert strategy_of(third).calls == []


def test_cancellation_between_stages_leaves_no_destination(source: Path, tmp_path: Path) -> None:
    token = CancellationToken()
    router = router_for(tool("a", hook=lambda _target: token.cancel()), tool("b"))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b")))
    target = target_for(tmp_path)

    with pytest.raises(Exception, match=r"[Cc]ancel"):
        run_pipeline(chain, source, target, router=router, cancellation=token)

    assert not target.destination.exists()


def test_cancellation_before_the_first_stage_runs_nothing(source: Path, tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()
    first = tool("a")
    router = router_for(first)

    with pytest.raises(Exception, match=r"[Cc]ancel"):
        run_pipeline(
            single_stage("a"), source, target_for(tmp_path), router=router, cancellation=token
        )

    assert strategy_of(first).calls == []


def test_parameters_reach_the_strategy(source: Path, tmp_path: Path) -> None:
    spec = tool("rotate", params=(ANGLE,))
    router = router_for(spec)
    chain = single_stage("rotate", params={"by": 180})

    run_pipeline(chain, source, target_for(tmp_path), router=router)

    assert strategy_of(spec).calls[0]["params"] == {"by": 180}


def test_the_result_records_the_pipeline_and_its_stages(source: Path, tmp_path: Path) -> None:
    router = router_for(tool("a"), tool("b"))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b")), name="clean")

    result = run_pipeline(chain, source, target_for(tmp_path), router=router)

    assert result.details["pipeline"] == "clean"
    assert result.details["stages"] == ["a", "b"]
    assert result.details["marker"] == "b", "the last stage's own details survive"


def test_progress_names_the_stage_when_there_is_more_than_one(source: Path, tmp_path: Path) -> None:
    progress = RecordingProgress()
    router = router_for(tool("a"), tool("b"))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b")))

    run_pipeline(chain, source, target_for(tmp_path), router=router, progress=progress)

    assert progress.descriptions == ["[1/2] a: faking a", "[2/2] b: faking b"]


def test_a_single_stage_pipeline_adds_no_progress_noise(source: Path, tmp_path: Path) -> None:
    progress = RecordingProgress()
    router = router_for(tool("a"))

    run_pipeline(single_stage("a"), source, target_for(tmp_path), router=router, progress=progress)

    assert progress.descriptions == ["faking a"]


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


def test_a_dry_run_writes_nothing_and_runs_no_stage(source: Path, tmp_path: Path) -> None:
    first = tool("a")
    router = router_for(first, tool("b"))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b")))
    target = target_for(tmp_path)

    result = run_pipeline(chain, source, target, router=router, dry_run=True)

    assert not target.destination.exists()
    assert strategy_of(first).calls == []
    assert result.outputs == ()
    assert result.details["dry_run"] is True


def test_a_dry_run_reports_the_engine_of_every_stage(source: Path, tmp_path: Path) -> None:
    router = router_for(tool("a"), tool("b"))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b")))

    result = run_pipeline(chain, source, target_for(tmp_path), router=router, dry_run=True)

    stages = result.details["stages"]
    assert isinstance(stages, list)
    assert [entry["tool"] for entry in stages] == ["a", "b"]
    assert all(entry["engine"] == "local" for entry in stages)


def test_a_dry_run_still_validates(source: Path, tmp_path: Path) -> None:
    router = router_for(tool("a"))

    with pytest.raises(InvalidParameterError, match="Unknown tool"):
        run_pipeline(
            single_stage("ghost"), source, target_for(tmp_path), router=router, dry_run=True
        )


# ---------------------------------------------------------------------------
# The router is the only way through
# ---------------------------------------------------------------------------


def test_every_stage_goes_through_the_router(source: Path, tmp_path: Path) -> None:
    """No stage may reach a strategy except by being routed to it.

    Asserted by giving the router a tool the pipeline does not name: if the
    pipeline could reach implementations directly, the count would not match.
    """
    a, b, unused = tool("a"), tool("b"), tool("unused")
    router: EngineRouter = router_for(a, b, unused)
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b")))

    run_pipeline(chain, source, target_for(tmp_path), router=router)

    assert len(strategy_of(a).calls) == 1
    assert len(strategy_of(b).calls) == 1
    assert strategy_of(unused).calls == []
