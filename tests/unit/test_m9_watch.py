"""Folder watch: settling, the digest key, and the rule v2 did not have.

The rule first, because it is the whole reason this is not v2's watcher: the
output directory may not be inside the watched tree and the watched tree may not
be inside the output directory. v2 wrote `_preprocessed.png` beside its input,
saw it as new input, and fed on itself. Both directions are refused here, before
the loop starts.

No test sleeps. ``watch_folder`` takes its ``sleep`` as an argument, so
:class:`Ticker` drives the loop tick by tick and stops it — which is what makes
a suite about a long-running process finish in milliseconds.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from docmax.core.cancellation import CancellationToken
from docmax.core.errors import CorruptDocumentError, InvalidParameterError
from docmax.runners.pipeline import Pipeline, Stage, single_stage
from docmax.runners.watch import WatchOptions, watch_folder
from tests.unit.m9_support import (
    RecordingProgress,
    document,
    markers,
    router_for,
    strategy_of,
    tool,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from docmax.runners.batch import ItemOutcome


class Ticker:
    """A stand-in for ``time.sleep`` that advances a script and then stops.

    Each call runs the next step, if any, and cancels once the script is spent.
    The loop scans, sleeps, scans again — so a file already present when the
    watch starts needs two scans to settle, and therefore one step in between.
    """

    def __init__(self, token: CancellationToken, steps: Sequence[Callable[[], None]] = ()) -> None:
        self.token = token
        self.steps = list(steps)
        self.count = 0

    def __call__(self, seconds: float) -> None:
        self.count += 1
        if self.steps:
            self.steps.pop(0)()
        else:
            self.token.cancel()


def nothing() -> None:
    """One tick in which the world does not change."""


@pytest.fixture
def folders(tmp_path: Path) -> tuple[Path, Path]:
    watched = tmp_path / "inbox"
    outputs = tmp_path / "done"
    watched.mkdir()
    outputs.mkdir()
    return watched, outputs


def options(watched: Path, outputs: Path, **kwargs: object) -> WatchOptions:
    return WatchOptions(folder=watched, output_dir=outputs, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The containment rule
# ---------------------------------------------------------------------------


def test_an_output_directory_inside_the_watched_tree_is_refused(
    folders: tuple[Path, Path],
) -> None:
    watched, _ = folders
    inside = watched / "done"
    inside.mkdir()
    spec = tool("a")

    with pytest.raises(InvalidParameterError, match="overlaps the watched folder"):
        watch_folder(single_stage("a"), options(watched, inside), router=router_for(spec))

    assert strategy_of(spec).calls == []


def test_a_watched_directory_inside_the_output_tree_is_refused(tmp_path: Path) -> None:
    outputs = tmp_path / "all"
    watched = outputs / "inbox"
    watched.mkdir(parents=True)

    with pytest.raises(InvalidParameterError, match="overlaps the watched folder"):
        watch_folder(single_stage("a"), options(watched, outputs), router=router_for(tool("a")))


def test_watching_and_writing_the_same_directory_is_refused(
    folders: tuple[Path, Path],
) -> None:
    watched, _ = folders

    with pytest.raises(InvalidParameterError, match="overlaps"):
        watch_folder(single_stage("a"), options(watched, watched), router=router_for(tool("a")))


def test_a_missing_watched_folder_is_refused(tmp_path: Path) -> None:
    outputs = tmp_path / "out"
    outputs.mkdir()

    with pytest.raises(InvalidParameterError, match="folder to watch does not exist"):
        watch_folder(
            single_stage("a"), options(tmp_path / "absent", outputs), router=router_for(tool("a"))
        )


def test_a_missing_output_directory_is_refused(tmp_path: Path) -> None:
    watched = tmp_path / "inbox"
    watched.mkdir()

    with pytest.raises(InvalidParameterError, match="output directory does not exist"):
        watch_folder(
            single_stage("a"), options(watched, tmp_path / "absent"), router=router_for(tool("a"))
        )


def test_an_invalid_pipeline_is_refused_before_the_loop(
    folders: tuple[Path, Path],
) -> None:
    watched, outputs = folders

    with pytest.raises(InvalidParameterError, match="Unknown tool"):
        watch_folder(single_stage("ghost"), options(watched, outputs), router=router_for(tool("a")))


# ---------------------------------------------------------------------------
# Option validation
# ---------------------------------------------------------------------------


def test_a_non_positive_interval_is_refused(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders

    with pytest.raises(InvalidParameterError, match="must be positive"):
        options(watched, outputs, interval=0)


def test_fewer_than_two_settle_ticks_is_refused(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders

    with pytest.raises(InvalidParameterError, match="at least twice"):
        options(watched, outputs, settle_ticks=1)


def test_no_pattern_is_refused(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders

    with pytest.raises(InvalidParameterError, match="No filename pattern"):
        options(watched, outputs, patterns=())


# ---------------------------------------------------------------------------
# Detection and settling
# ---------------------------------------------------------------------------


def test_a_file_already_present_is_processed_once_it_has_settled(
    folders: tuple[Path, Path],
) -> None:
    watched, outputs = folders
    document(watched / "alpha.pdf", text="alpha")
    token = CancellationToken()
    router = router_for(tool("a"))

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router,
        cancellation=token,
        sleep=Ticker(token, [nothing]),
    )

    assert (outputs / "alpha.pdf").exists()
    assert markers(outputs / "alpha.pdf") == ["a"]
    assert [item.source.name for item in report.outcomes] == ["alpha.pdf"]


def test_a_file_that_arrives_during_the_watch_is_picked_up(
    folders: tuple[Path, Path],
) -> None:
    watched, outputs = folders
    token = CancellationToken()

    def arrive() -> None:
        document(watched / "late.pdf", text="late")

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(tool("a")),
        cancellation=token,
        # scan (empty) · arrive · scan (ticks=1) · nothing · scan (ticks=2, runs)
        sleep=Ticker(token, [arrive, nothing]),
    )

    assert [item.source.name for item in report.outcomes] == ["late.pdf"]


def test_a_file_still_being_written_is_not_processed(
    folders: tuple[Path, Path],
) -> None:
    """The settle check: size changed between scans, so it waits."""
    watched, outputs = folders
    growing = watched / "big.pdf"
    growing.write_bytes(b"partial")
    token = CancellationToken()
    spec = tool("a")

    def grow() -> None:
        growing.write_bytes(b"partial-and-more")

    watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(spec),
        cancellation=token,
        # scan (ticks=1) · grow · scan (fingerprint changed, back to 1) · stop
        sleep=Ticker(token, [grow]),
    )

    assert strategy_of(spec).calls == []
    assert list(outputs.iterdir()) == []


def test_a_file_that_stops_changing_is_processed_on_the_next_tick(
    folders: tuple[Path, Path],
) -> None:
    watched, outputs = folders
    growing = watched / "big.pdf"
    growing.write_bytes(b"partial")
    token = CancellationToken()

    def grow() -> None:
        growing.write_bytes(b"partial-and-more")

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(tool("a")),
        cancellation=token,
        # scan · grow · scan (reset) · nothing · scan (settled, runs)
        sleep=Ticker(token, [grow, nothing]),
    )

    assert [item.source.name for item in report.outcomes] == ["big.pdf"]


def test_only_matching_patterns_are_considered(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders
    document(watched / "keep.pdf", text="keep")
    document(watched / "skip.txt", text="skip")
    token = CancellationToken()

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs, patterns=("*.pdf",)),
        router=router_for(tool("a")),
        cancellation=token,
        sleep=Ticker(token, [nothing]),
    )

    assert [item.source.name for item in report.outcomes] == ["keep.pdf"]


def test_several_patterns_are_all_matched(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders
    document(watched / "a.pdf", text="one")
    document(watched / "b.txt", text="two")
    token = CancellationToken()

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs, patterns=("*.pdf", "*.txt")),
        router=router_for(tool("a")),
        cancellation=token,
        sleep=Ticker(token, [nothing]),
    )

    assert sorted(item.source.name for item in report.outcomes) == ["a.pdf", "b.txt"]


def test_a_subdirectory_is_not_descended_into(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders
    nested = watched / "deeper"
    nested.mkdir()
    document(nested / "hidden.pdf", text="hidden")
    token = CancellationToken()

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(tool("a")),
        cancellation=token,
        sleep=Ticker(token, [nothing]),
    )

    assert report.outcomes == ()


# ---------------------------------------------------------------------------
# The digest key
# ---------------------------------------------------------------------------


def test_the_same_file_is_processed_only_once(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders
    document(watched / "alpha.pdf", text="alpha")
    token = CancellationToken()
    spec = tool("a")

    watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(spec),
        cancellation=token,
        # Four more scans after it is processed; none may run it again.
        sleep=Ticker(token, [nothing, nothing, nothing, nothing]),
    )

    assert len(strategy_of(spec).calls) == 1


def test_identical_content_under_a_second_name_is_not_reprocessed(
    folders: tuple[Path, Path],
) -> None:
    """The key is the content, so a duplicate delivery is not work."""
    watched, outputs = folders
    document(watched / "alpha.pdf", text="same")
    document(watched / "copy.pdf", text="same")
    token = CancellationToken()
    spec = tool("a")

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(spec),
        cancellation=token,
        sleep=Ticker(token, [nothing]),
    )

    assert len(strategy_of(spec).calls) == 1
    assert len(report.outcomes) == 1


def test_different_content_under_two_names_is_both_processed(
    folders: tuple[Path, Path],
) -> None:
    watched, outputs = folders
    document(watched / "alpha.pdf", text="one")
    document(watched / "beta.pdf", text="two")
    token = CancellationToken()

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(tool("a")),
        cancellation=token,
        sleep=Ticker(token, [nothing]),
    )

    assert sorted(item.source.name for item in report.outcomes) == ["alpha.pdf", "beta.pdf"]


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_a_failing_document_does_not_stop_the_watch(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders
    document(watched / "bad.pdf", text="bad")
    token = CancellationToken()

    def add_good() -> None:
        document(watched / "good.pdf", text="good")

    def fail_on_bad(target: object) -> None:
        destination = getattr(target, "destination", None)
        if destination is not None and Path(destination).stem == "bad":
            raise CorruptDocumentError("unreadable")

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(tool("a", hook=fail_on_bad)),
        cancellation=token,
        sleep=Ticker(token, [add_good, nothing, nothing]),
    )

    assert [item.source.stem for item in report.failed] == ["bad"]
    assert [item.source.stem for item in report.succeeded] == ["good"]
    assert (outputs / "good.pdf").exists()


def test_a_failed_document_is_not_retried(folders: tuple[Path, Path]) -> None:
    """Otherwise a permanently corrupt file fails every tick, forever."""
    watched, outputs = folders
    document(watched / "bad.pdf", text="bad")
    token = CancellationToken()
    spec = tool("a", raises=CorruptDocumentError("unreadable"))

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(spec),
        cancellation=token,
        sleep=Ticker(token, [nothing, nothing, nothing]),
    )

    assert len(strategy_of(spec).calls) == 1
    assert len(report.failed) == 1


def test_a_failed_document_leaves_no_output(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders
    document(watched / "bad.pdf", text="bad")
    token = CancellationToken()

    watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(tool("a", raises=CorruptDocumentError("no"))),
        cancellation=token,
        sleep=Ticker(token, [nothing]),
    )

    assert list(outputs.iterdir()) == []


def test_an_existing_output_is_a_failure_not_a_crash(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders
    document(watched / "alpha.pdf", text="alpha")
    document(outputs / "alpha.pdf", text="already here")
    token = CancellationToken()

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(tool("a")),
        cancellation=token,
        sleep=Ticker(token, [nothing]),
    )

    assert report.failed[0].error is not None
    assert report.failed[0].error.code.value == "output.exists"
    assert (outputs / "alpha.pdf").read_text(encoding="utf-8") == "already here"


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


def test_cancellation_ends_the_watch(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders
    token = CancellationToken()
    ticker = Ticker(token)

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(tool("a")),
        cancellation=token,
        sleep=ticker,
    )

    assert report.cancelled
    assert ticker.count == 1, "the watch stopped on the first tick after cancellation"


def test_a_watch_cancelled_before_it_starts_does_nothing(
    folders: tuple[Path, Path],
) -> None:
    watched, outputs = folders
    document(watched / "alpha.pdf", text="alpha")
    token = CancellationToken()
    token.cancel()
    spec = tool("a")

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(spec),
        cancellation=token,
        sleep=Ticker(token),
    )

    assert report.outcomes == ()
    assert strategy_of(spec).calls == []


def test_the_report_carries_everything_processed_before_the_stop(
    folders: tuple[Path, Path],
) -> None:
    watched, outputs = folders
    document(watched / "alpha.pdf", text="alpha")
    token = CancellationToken()

    report = watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(tool("a")),
        cancellation=token,
        sleep=Ticker(token, [nothing]),
    )

    assert report.cancelled
    assert len(report.succeeded) == 1


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_each_outcome_is_announced_as_it_happens(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders
    document(watched / "alpha.pdf", text="alpha")
    token = CancellationToken()
    seen: list[ItemOutcome] = []

    watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(tool("a")),
        cancellation=token,
        on_outcome=seen.append,
        sleep=Ticker(token, [nothing]),
    )

    assert [item.source.name for item in seen] == ["alpha.pdf"]


def test_progress_names_the_document(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders
    document(watched / "alpha.pdf", text="alpha")
    token = CancellationToken()
    progress = RecordingProgress()

    watch_folder(
        single_stage("a"),
        options(watched, outputs),
        router=router_for(tool("a")),
        cancellation=token,
        progress=progress,
        sleep=Ticker(token, [nothing]),
    )

    assert progress.descriptions == ["alpha.pdf faking a"]


def test_a_pipeline_runs_over_each_watched_document(folders: tuple[Path, Path]) -> None:
    watched, outputs = folders
    document(watched / "alpha.pdf", text="alpha")
    token = CancellationToken()
    router = router_for(tool("a"), tool("b"))
    chain = Pipeline(stages=(Stage(tool="a"), Stage(tool="b")))

    watch_folder(
        chain,
        options(watched, outputs),
        router=router,
        cancellation=token,
        sleep=Ticker(token, [nothing]),
    )

    assert markers(outputs / "alpha.pdf") == ["a", "b"]
