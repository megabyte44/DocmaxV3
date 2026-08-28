"""Batch: naming, contamination, and the isolation of one failure from the rest.

Three of v2's defects are asserted against directly here, because "we fixed
that" is not a property a codebase keeps on its own:

* a batch that wrote over its own inputs,
* a batch that died entirely when one document failed,
* a batch that flattened every failure to a string and lost its type.

The two contamination checks are the ones worth reading first. They are fatal
and they run before any document is touched, because a batch that has already
rewritten item seven's source by the time it reaches item seven cannot be
rescued by skipping item seven.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docmax.core.cancellation import CancellationToken
from docmax.core.errors import (
    CancelledError,
    CorruptDocumentError,
    DocMaxError,
    EncryptedDocumentError,
    InputNotFoundError,
    InvalidParameterError,
    OutputExistsError,
)
from docmax.runners.batch import ItemOutcome, plan_batch, run_batch
from docmax.runners.pipeline import Pipeline, Stage, single_stage
from tests.unit.m9_support import (
    REQUIRED_TO,
    RecordingProgress,
    document,
    markers,
    router_for,
    strategy_of,
    tool,
)


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """An input directory with three documents, and an empty output directory."""
    inputs = tmp_path / "in"
    outputs = tmp_path / "out"
    inputs.mkdir()
    outputs.mkdir()
    for name in ("alpha", "beta", "gamma"):
        document(inputs / f"{name}.pdf", text=name)
    return inputs, outputs


def sources(inputs: Path) -> list[Path]:
    return sorted(inputs.glob("*.pdf"))


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def test_every_input_is_mirrored_into_the_output_directory(
    workspace: tuple[Path, Path],
) -> None:
    inputs, outputs = workspace
    router = router_for(tool("a"))

    report = run_batch(single_stage("a"), sources(inputs), outputs, router=router)

    assert report.ok
    assert sorted(path.name for path in outputs.iterdir()) == [
        "alpha.pdf",
        "beta.pdf",
        "gamma.pdf",
    ]


def test_the_output_extension_comes_from_the_final_tool(
    workspace: tuple[Path, Path],
) -> None:
    inputs, outputs = workspace
    router = router_for(tool("render", suffix=".txt"))

    run_batch(single_stage("render"), sources(inputs), outputs, router=router)

    assert sorted(path.suffix for path in outputs.iterdir()) == [".txt", ".txt", ".txt"]


def test_the_extension_of_a_pipeline_comes_from_its_last_stage(
    workspace: tuple[Path, Path],
) -> None:
    inputs, outputs = workspace
    router = router_for(tool("a"), tool("render", suffix=".txt"))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="render")))

    run_batch(chain, sources(inputs), outputs, router=router)

    assert all(path.suffix == ".txt" for path in outputs.iterdir())


def test_each_document_goes_through_the_whole_pipeline(
    workspace: tuple[Path, Path],
) -> None:
    inputs, outputs = workspace
    router = router_for(tool("a"), tool("b"))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b")))

    run_batch(chain, sources(inputs), outputs, router=router)

    assert markers(outputs / "alpha.pdf") == ["a", "b"]


# ---------------------------------------------------------------------------
# Contamination — refused before anything runs
# ---------------------------------------------------------------------------


def test_two_inputs_mirroring_to_one_name_are_refused(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    outputs = tmp_path / "out"
    for directory in (first, second, outputs):
        directory.mkdir()
    document(first / "report.pdf")
    document(second / "report.pdf")

    spec = tool("a")
    router = router_for(spec)

    with pytest.raises(InvalidParameterError, match="would both be written"):
        run_batch(
            single_stage("a"),
            [first / "report.pdf", second / "report.pdf"],
            outputs,
            router=router,
        )

    assert strategy_of(spec).calls == [], "nothing may run before the plan is known good"
    assert list(outputs.iterdir()) == []


def test_a_destination_that_is_another_items_input_is_refused(tmp_path: Path) -> None:
    """`OutputTarget` cannot see this: it compares against the inputs of one call."""
    shared = tmp_path / "shared"
    shared.mkdir()
    document(shared / "alpha.pdf", text="alpha")
    document(shared / "beta.pdf", text="beta")

    spec = tool("a")
    router = router_for(spec)

    with pytest.raises(InvalidParameterError, match="would overwrite an input"):
        run_batch(single_stage("a"), sources(shared), shared, router=router)

    assert strategy_of(spec).calls == []
    assert (shared / "alpha.pdf").read_text(encoding="utf-8") == "alpha"


def test_an_output_directory_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    inputs = tmp_path / "in"
    inputs.mkdir()
    document(inputs / "a.pdf")

    with pytest.raises(InvalidParameterError, match="does not exist"):
        run_batch(
            single_stage("a"), sources(inputs), tmp_path / "absent", router=router_for(tool("a"))
        )


def test_no_inputs_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidParameterError, match="No input documents"):
        plan_batch([], tmp_path, suffix=".pdf")


def test_a_tool_whose_extension_depends_on_a_parameter_is_refused(
    workspace: tuple[Path, Path],
) -> None:
    inputs, outputs = workspace
    router = router_for(tool("convert", params=(REQUIRED_TO,)))
    chain = single_stage("convert", params={"to": "html"})

    with pytest.raises(InvalidParameterError, match="cannot name the outputs"):
        run_batch(chain, sources(inputs), outputs, router=router)


def test_an_invalid_pipeline_is_refused_before_the_plan(
    workspace: tuple[Path, Path],
) -> None:
    inputs, outputs = workspace

    with pytest.raises(InvalidParameterError, match="Unknown tool"):
        run_batch(single_stage("ghost"), sources(inputs), outputs, router=router_for(tool("a")))


# ---------------------------------------------------------------------------
# Isolation of failures
# ---------------------------------------------------------------------------


def failing_on(name: str, error: DocMaxError) -> object:
    """A hook that raises for one named document and passes every other."""

    def hook(target: object) -> None:
        destination = getattr(target, "destination", None)
        if destination is not None and Path(destination).stem == name:
            raise error

    return hook


def test_one_failing_item_does_not_stop_the_batch(workspace: tuple[Path, Path]) -> None:
    inputs, outputs = workspace
    router = router_for(
        tool("a", hook=failing_on("beta", CorruptDocumentError("beta is broken")))  # type: ignore[arg-type]
    )

    report = run_batch(single_stage("a"), sources(inputs), outputs, router=router)

    assert len(report.outcomes) == 3
    assert [item.source.stem for item in report.failed] == ["beta"]
    assert [item.source.stem for item in report.succeeded] == ["alpha", "gamma"]


def test_a_failed_item_leaves_every_other_output_intact(
    workspace: tuple[Path, Path],
) -> None:
    inputs, outputs = workspace
    router = router_for(
        tool("a", hook=failing_on("beta", CorruptDocumentError("no")))  # type: ignore[arg-type]
    )

    run_batch(single_stage("a"), sources(inputs), outputs, router=router)

    assert sorted(path.name for path in outputs.iterdir()) == ["alpha.pdf", "gamma.pdf"]
    assert markers(outputs / "gamma.pdf") == ["a"]


def test_a_failed_item_writes_no_partial_output(workspace: tuple[Path, Path]) -> None:
    inputs, outputs = workspace
    router = router_for(
        tool("a", hook=failing_on("beta", CorruptDocumentError("no")))  # type: ignore[arg-type]
    )

    run_batch(single_stage("a"), sources(inputs), outputs, router=router)

    assert not (outputs / "beta.pdf").exists()
    assert not list(outputs.glob(".docmax-*")), "a staged file survived the failure"


def test_the_typed_error_survives_rather_than_becoming_a_string(
    workspace: tuple[Path, Path],
) -> None:
    """v2 flattened failures to (path, str(exc)) and lost the type of each."""
    inputs, outputs = workspace
    router = router_for(
        tool("a", hook=failing_on("beta", EncryptedDocumentError("locked")))  # type: ignore[arg-type]
    )

    report = run_batch(single_stage("a"), sources(inputs), outputs, router=router)

    error = report.failed[0].error
    assert isinstance(error, EncryptedDocumentError)
    assert error.code.value == "input.encrypted"


def test_failures_are_available_as_an_exception_group(
    workspace: tuple[Path, Path],
) -> None:
    """ADR 0001 raised the Python floor partly for this."""
    inputs, outputs = workspace
    router = router_for(
        tool("a", hook=failing_on("beta", CorruptDocumentError("no")))  # type: ignore[arg-type]
    )

    report = run_batch(single_stage("a"), sources(inputs), outputs, router=router)
    group = report.as_exception_group()

    assert group is not None
    assert len(group.exceptions) == 1
    matched, _ = group.split(CorruptDocumentError)
    assert matched is not None


def test_a_clean_batch_has_no_exception_group(workspace: tuple[Path, Path]) -> None:
    inputs, outputs = workspace
    report = run_batch(single_stage("a"), sources(inputs), outputs, router=router_for(tool("a")))

    assert report.as_exception_group() is None


def test_a_missing_input_is_one_failed_item_not_a_dead_batch(
    workspace: tuple[Path, Path],
) -> None:
    inputs, outputs = workspace
    router = router_for(tool("a"))
    paths = [*sources(inputs), inputs / "absent.pdf"]

    report = run_batch(single_stage("a"), paths, outputs, router=router)

    assert isinstance(report.failed[0].error, InputNotFoundError)
    assert len(report.succeeded) == 3


def test_an_existing_output_is_one_failed_item(workspace: tuple[Path, Path]) -> None:
    inputs, outputs = workspace
    document(outputs / "beta.pdf", text="already here")

    report = run_batch(single_stage("a"), sources(inputs), outputs, router=router_for(tool("a")))

    assert isinstance(report.failed[0].error, OutputExistsError)
    assert (outputs / "beta.pdf").read_text(encoding="utf-8") == "already here"


def test_force_overwrites_an_existing_output(workspace: tuple[Path, Path]) -> None:
    inputs, outputs = workspace
    document(outputs / "beta.pdf", text="stale")

    report = run_batch(
        single_stage("a"), sources(inputs), outputs, router=router_for(tool("a")), force=True
    )

    assert report.ok
    assert markers(outputs / "beta.pdf") == ["a"]


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancellation_stops_scheduling_further_documents(
    workspace: tuple[Path, Path],
) -> None:
    inputs, outputs = workspace
    token = CancellationToken()
    spec = tool("a", hook=lambda _target: token.cancel())
    router = router_for(spec)

    report = run_batch(
        single_stage("a"), sources(inputs), outputs, router=router, cancellation=token
    )

    assert report.cancelled
    assert len(strategy_of(spec).calls) == 1, "only the document already under way ran"
    assert sorted(path.name for path in outputs.iterdir()) == ["alpha.pdf"]


def test_a_document_cancelled_mid_run_writes_nothing(
    workspace: tuple[Path, Path],
) -> None:
    inputs, outputs = workspace
    token = CancellationToken()
    router = router_for(tool("a", raises=CancelledError("stopped")))

    report = run_batch(
        single_stage("a"), sources(inputs), outputs, router=router, cancellation=token
    )

    assert report.cancelled
    assert report.outcomes == ()
    assert list(outputs.iterdir()) == []


def test_a_cancelled_batch_is_not_reported_as_ok(workspace: tuple[Path, Path]) -> None:
    inputs, outputs = workspace
    token = CancellationToken()
    token.cancel()

    report = run_batch(
        single_stage("a"),
        sources(inputs),
        outputs,
        router=router_for(tool("a")),
        cancellation=token,
    )

    assert not report.ok
    assert report.outcomes == ()


# ---------------------------------------------------------------------------
# Progress and reporting
# ---------------------------------------------------------------------------


def test_progress_names_the_document_being_processed(
    workspace: tuple[Path, Path],
) -> None:
    inputs, outputs = workspace
    progress = RecordingProgress()

    run_batch(
        single_stage("a"), sources(inputs), outputs, router=router_for(tool("a")), progress=progress
    )

    assert progress.descriptions == [
        "[1/3] alpha.pdf faking a",
        "[2/3] beta.pdf faking a",
        "[3/3] gamma.pdf faking a",
    ]


def test_progress_names_both_the_document_and_the_stage(
    workspace: tuple[Path, Path],
) -> None:
    inputs, outputs = workspace
    progress = RecordingProgress()
    router = router_for(tool("a"), tool("b"))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b")))

    run_batch(chain, sources(inputs)[:1], outputs, router=router, progress=progress)

    assert progress.descriptions == [
        "[1/1] alpha.pdf [1/2] a: faking a",
        "[1/1] alpha.pdf [2/2] b: faking b",
    ]


def test_outcomes_are_reported_as_they_happen(workspace: tuple[Path, Path]) -> None:
    inputs, outputs = workspace
    seen: list[ItemOutcome] = []

    run_batch(
        single_stage("a"),
        sources(inputs),
        outputs,
        router=router_for(tool("a")),
        on_outcome=seen.append,
    )

    assert [item.source.stem for item in seen] == ["alpha", "beta", "gamma"]


def test_a_dry_run_writes_nothing(workspace: tuple[Path, Path]) -> None:
    inputs, outputs = workspace
    spec = tool("a")

    report = run_batch(
        single_stage("a"), sources(inputs), outputs, router=router_for(spec), dry_run=True
    )

    assert report.ok
    assert list(outputs.iterdir()) == []
    assert strategy_of(spec).calls == []


def test_documents_are_processed_in_the_order_given(
    workspace: tuple[Path, Path],
) -> None:
    inputs, outputs = workspace
    given = list(reversed(sources(inputs)))

    report = run_batch(single_stage("a"), given, outputs, router=router_for(tool("a")))

    assert [item.source.stem for item in report.outcomes] == ["gamma", "beta", "alpha"]
