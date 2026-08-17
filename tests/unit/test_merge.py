"""`merge` — the reference tool, tested as the template future tools follow.

Four things are being demonstrated here, and only the first is about merging:

* the operation itself — order preserved, page counts right, outline correct;
* the **contract**, which every tool must satisfy: typed errors for bad input,
  progress reported, cancellation observed, a `ToolResult` returned;
* the **safety guarantee**, that a failure at any point leaves the destination
  exactly as it was — the reason `core/atomic.py` exists;
* the **integration**, that the registry can find this tool and the router can
  run it without either knowing anything about PDFs.

Fixtures are generated with pypdf rather than committed, so the corpus cannot
drift from the generator and no test depends on a file anyone has to keep.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from docmax.core.cancellation import NEVER_CANCELLED, CancellationToken
from docmax.core.config import Config
from docmax.core.errors import (
    CancelledError,
    CorruptDocumentError,
    EncryptedDocumentError,
    InvalidParameterError,
    OutputValidationError,
    UnsupportedFormatError,
)
from docmax.core.models import DocumentRef, Engine, OutputTarget
from docmax.core.protocols import NULL_PROGRESS
from docmax.core.registry import get_tool
from docmax.core.router import EngineRouter
from docmax.tools.merge.local import MergeLocal, build
from docmax.tools.merge.validators import is_readable_pdf, page_count_is

if TYPE_CHECKING:
    from docmax.core.protocols import ProgressSink


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def write_pdf(path: Path, pages: int, *, encrypt: str | None = None) -> Path:
    """A minimal but genuine PDF with ``pages`` blank pages."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if encrypt is not None:
        writer.encrypt(encrypt)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def page_count(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def outline_titles(path: Path) -> list[str]:
    """Top-level outline entry titles, in document order.

    `PdfReader.outline` is a nested, heterogeneous structure — nested lists for
    sub-outlines — so the titles are pulled out explicitly rather than trusted
    to be uniform.
    """
    from pypdf import PdfReader

    titles: list[str] = []
    for item in PdfReader(str(path)).outline:
        title = getattr(item, "title", None)
        if isinstance(title, str):
            titles.append(title)
    return titles


def docs(*paths: Path) -> list[DocumentRef]:
    return [DocumentRef.from_path(p) for p in paths]


def target_at(path: Path) -> OutputTarget:
    return OutputTarget(destination=path, force=True)


def staged(directory: Path) -> list[str]:
    """Anything the atomic writer left behind. Staged names begin with a dot."""
    return sorted(p.name for p in directory.glob(".*"))


class RecordingProgress:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def start(self, description: str, *, total: int | None = None) -> None:
        self.events.append(("start", (description, total)))

    def advance(self, amount: int = 1) -> None:
        self.events.append(("advance", amount))

    def finish(self) -> None:
        self.events.append(("finish", None))


@pytest.fixture
def two_pdfs(tmp_path: Path) -> tuple[Path, Path]:
    return write_pdf(tmp_path / "a.pdf", 2), write_pdf(tmp_path / "b.pdf", 3)


@pytest.fixture
def strategy() -> MergeLocal:
    return MergeLocal()


def run(
    strategy: MergeLocal,
    sources: list[DocumentRef],
    destination: Path,
    *,
    progress: ProgressSink = NULL_PROGRESS,
    cancellation: CancellationToken = NEVER_CANCELLED,
    **params: Any,
) -> Any:
    return strategy.run(
        sources,
        target_at(destination),
        progress=progress,
        cancellation=cancellation,
        **params,
    )


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


def test_two_documents_merge(
    strategy: MergeLocal, two_pdfs: tuple[Path, Path], tmp_path: Path
) -> None:
    a, b = two_pdfs
    out = tmp_path / "merged.pdf"

    result = run(strategy, docs(a, b), out)

    assert out.is_file()
    assert page_count(out) == 5, "2 + 3"
    assert result.details["pages"] == 5


def test_pages_appear_in_the_order_given(strategy: MergeLocal, tmp_path: Path) -> None:
    """The order is the user's instruction, not an implementation detail.

    Each source is given a distinct page count, so the boundaries in the merged
    outline reveal the order unambiguously.
    """
    first = write_pdf(tmp_path / "first.pdf", 1)
    second = write_pdf(tmp_path / "second.pdf", 2)
    third = write_pdf(tmp_path / "third.pdf", 3)
    out = tmp_path / "merged.pdf"

    run(strategy, docs(third, first, second), out)

    assert page_count(out) == 6
    assert outline_titles(out) == ["third", "first", "second"]


def test_a_single_document_is_a_valid_merge(strategy: MergeLocal, tmp_path: Path) -> None:
    """A script merging a variable-length list must not crash on a list of one.

    Whether one input is a mistake is a question the *interface* can answer —
    it knows whether the user typed one path or a glob matched one file. Here
    it is simply a validated, atomic copy.
    """
    source = write_pdf(tmp_path / "only.pdf", 4)
    out = tmp_path / "merged.pdf"

    run(strategy, docs(source), out)

    assert page_count(out) == 4


def test_many_documents_merge(strategy: MergeLocal, tmp_path: Path) -> None:
    sources = [write_pdf(tmp_path / f"{i}.pdf", 1) for i in range(10)]
    out = tmp_path / "merged.pdf"

    run(strategy, docs(*sources), out)

    assert page_count(out) == 10


# ---------------------------------------------------------------------------
# The outline parameter
# ---------------------------------------------------------------------------


def test_an_outline_entry_is_added_per_source(
    strategy: MergeLocal, two_pdfs: tuple[Path, Path], tmp_path: Path
) -> None:
    a, b = two_pdfs
    out = tmp_path / "merged.pdf"

    run(strategy, docs(a, b), out)

    assert outline_titles(out) == ["a", "b"]


def test_the_outline_can_be_turned_off(
    strategy: MergeLocal, two_pdfs: tuple[Path, Path], tmp_path: Path
) -> None:
    a, b = two_pdfs
    out = tmp_path / "merged.pdf"

    run(strategy, docs(a, b), out, outline=False)

    assert outline_titles(out) == []
    assert page_count(out) == 5, "the pages are unaffected"


def test_a_non_boolean_outline_is_refused(
    strategy: MergeLocal, two_pdfs: tuple[Path, Path], tmp_path: Path
) -> None:
    """`outline="no"` is truthy, and would silently mean the opposite."""
    a, b = two_pdfs

    with pytest.raises(InvalidParameterError) as caught:
        run(strategy, docs(a, b), tmp_path / "merged.pdf", outline="no")

    assert caught.value.context["parameter"] == "outline"


def test_an_unknown_parameter_is_ignored(
    strategy: MergeLocal, two_pdfs: tuple[Path, Path], tmp_path: Path
) -> None:
    """The protocol is `**params`; the CLI and server validate against the ParamSpec."""
    a, b = two_pdfs
    out = tmp_path / "merged.pdf"

    run(strategy, docs(a, b), out, something_else=1)

    assert page_count(out) == 5


# ---------------------------------------------------------------------------
# Input validation — a distinct error per distinct problem
# ---------------------------------------------------------------------------


def test_no_documents_is_a_parameter_error(strategy: MergeLocal, tmp_path: Path) -> None:
    with pytest.raises(InvalidParameterError) as caught:
        run(strategy, [], tmp_path / "merged.pdf")

    assert caught.value.remedy


def test_a_non_pdf_input_is_refused(strategy: MergeLocal, tmp_path: Path) -> None:
    """A Word document needs a different next step from a damaged PDF."""
    source = tmp_path / "notes.txt"
    source.write_text("this is not a pdf", encoding="utf-8")

    with pytest.raises(UnsupportedFormatError):
        run(strategy, docs(source), tmp_path / "merged.pdf")


def test_a_corrupt_pdf_is_refused(strategy: MergeLocal, tmp_path: Path) -> None:
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"%PDF-1.7\nbut the rest is garbage")

    with pytest.raises(CorruptDocumentError) as caught:
        run(strategy, docs(source), tmp_path / "merged.pdf")

    assert "broken.pdf" in str(caught.value)


def test_an_encrypted_pdf_names_the_password_as_the_problem(
    strategy: MergeLocal, tmp_path: Path
) -> None:
    source = write_pdf(tmp_path / "locked.pdf", 1, encrypt="secret")

    with pytest.raises(EncryptedDocumentError) as caught:
        run(strategy, docs(source), tmp_path / "merged.pdf")

    assert "unlock" in (caught.value.remedy or "").lower()


def test_a_missing_input_fails_at_the_boundary(tmp_path: Path) -> None:
    """`DocumentRef.from_path` refuses before any work begins."""
    from docmax.core.errors import InputNotFoundError

    with pytest.raises(InputNotFoundError):
        DocumentRef.from_path(tmp_path / "absent.pdf")


def test_a_bad_input_partway_through_leaves_nothing_behind(
    strategy: MergeLocal, tmp_path: Path
) -> None:
    """The first file is fine, the second is not — and nothing lands."""
    good = write_pdf(tmp_path / "good.pdf", 2)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7\ngarbage")
    out = tmp_path / "merged.pdf"

    with pytest.raises(CorruptDocumentError):
        run(strategy, docs(good, bad), out)

    assert not out.exists()
    assert staged(tmp_path) == []


# ---------------------------------------------------------------------------
# Atomic output — the guarantee core/atomic.py exists for
# ---------------------------------------------------------------------------


def test_an_existing_destination_survives_a_failure(strategy: MergeLocal, tmp_path: Path) -> None:
    """The v2 bug this project was rebuilt to make unreachable."""
    out = tmp_path / "merged.pdf"
    out.write_bytes(b"a previous run's output")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7\ngarbage")

    with pytest.raises(CorruptDocumentError):
        run(strategy, docs(bad), out)

    assert out.read_bytes() == b"a previous run's output"
    assert staged(tmp_path) == []


def test_cancellation_midway_leaves_the_destination_untouched(
    strategy: MergeLocal, tmp_path: Path
) -> None:
    out = tmp_path / "merged.pdf"
    out.write_bytes(b"a previous run's output")
    sources = [write_pdf(tmp_path / f"{i}.pdf", 1) for i in range(4)]

    token = CancellationToken()

    class CancelAfterFirst:
        def __init__(self) -> None:
            self.seen = 0

        def start(self, description: str, *, total: int | None = None) -> None: ...

        def advance(self, amount: int = 1) -> None:
            self.seen += 1
            if self.seen == 1:
                token.cancel()

        def finish(self) -> None: ...

        pass

    with pytest.raises(CancelledError):
        run(strategy, docs(*sources), out, progress=CancelAfterFirst(), cancellation=token)

    assert out.read_bytes() == b"a previous run's output"
    assert staged(tmp_path) == []


def test_no_staged_file_survives_a_successful_run(
    strategy: MergeLocal, two_pdfs: tuple[Path, Path], tmp_path: Path
) -> None:
    """Staged files sit beside the destination, so a leak litters the user's directory."""
    a, b = two_pdfs

    run(strategy, docs(a, b), tmp_path / "merged.pdf")

    assert staged(tmp_path) == []


def test_a_failing_validator_prevents_delivery(tmp_path: Path) -> None:
    """The validators run against the staged file, before anything is swapped."""
    from docmax.core.atomic import atomic_write

    out = tmp_path / "out.pdf"
    out.write_bytes(b"the original")
    source = write_pdf(tmp_path / "src.pdf", 1)

    def write_a_one_page_pdf() -> None:
        with atomic_write(target_at(out), validators=(page_count_is(99),)) as handle:
            handle.write(source.read_bytes())

    with pytest.raises(OutputValidationError):
        write_a_one_page_pdf()

    assert out.read_bytes() == b"the original"


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def test_is_readable_pdf_accepts_a_real_pdf(tmp_path: Path) -> None:
    is_readable_pdf(write_pdf(tmp_path / "ok.pdf", 1))


def test_is_readable_pdf_rejects_rubbish(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")

    with pytest.raises(OutputValidationError):
        is_readable_pdf(bad)


def test_page_count_is_reports_the_mismatch(tmp_path: Path) -> None:
    produced = write_pdf(tmp_path / "ok.pdf", 3)

    with pytest.raises(OutputValidationError) as caught:
        page_count_is(5)(produced)

    assert caught.value.context["expected"] == 5
    assert caught.value.context["actual"] == 3


# ---------------------------------------------------------------------------
# The Core contract
# ---------------------------------------------------------------------------


def test_progress_is_started_and_advanced_once_per_document(
    strategy: MergeLocal, tmp_path: Path
) -> None:
    sources = [write_pdf(tmp_path / f"{i}.pdf", 1) for i in range(3)]
    progress = RecordingProgress()

    run(strategy, docs(*sources), tmp_path / "merged.pdf", progress=progress)

    names = [name for name, _ in progress.events]
    assert names == ["start", "advance", "advance", "advance"]
    _, payload = progress.events[0]
    assert payload == ("Merging 3 document(s)", 3)


def test_the_tool_does_not_finish_the_progress_sink(
    strategy: MergeLocal, two_pdfs: tuple[Path, Path], tmp_path: Path
) -> None:
    """Deliberate: the router closes the region in a `finally`, on every path.

    A tool that finished its own sink would double-finish on success and still
    leak on the paths it did not anticipate.
    """
    a, b = two_pdfs
    progress = RecordingProgress()

    run(strategy, docs(a, b), tmp_path / "merged.pdf", progress=progress)

    assert ("finish", None) not in progress.events


def test_an_already_cancelled_run_produces_nothing(
    strategy: MergeLocal, two_pdfs: tuple[Path, Path], tmp_path: Path
) -> None:
    a, b = two_pdfs
    out = tmp_path / "merged.pdf"
    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError) as caught:
        run(strategy, docs(a, b), out, cancellation=token)

    assert "merge" in str(caught.value)
    assert not out.exists()


def test_the_result_describes_the_operation(
    strategy: MergeLocal, two_pdfs: tuple[Path, Path], tmp_path: Path
) -> None:
    a, b = two_pdfs
    out = tmp_path / "merged.pdf"

    result = run(strategy, docs(a, b), out)

    assert result.outputs == (out,)
    assert result.engine_used is Engine.LOCAL
    assert result.engine_version is not None
    assert result.engine_version.startswith("pypdf/")
    assert result.duration_ms >= 0
    assert result.details["pages"] == 5


def test_availability_is_answered_without_importing_pypdf(strategy: MergeLocal) -> None:
    """`find_spec`, not an import — availability is asked on every routing decision."""
    assert strategy.is_available() is True
    assert strategy.unavailable_reason() is None


def test_build_returns_a_strategy() -> None:
    """Every strategy module exposes exactly this factory."""
    assert isinstance(build(), MergeLocal)


# ---------------------------------------------------------------------------
# Integration — discovery and execution, with neither knowing about PDFs
# ---------------------------------------------------------------------------


def test_the_registry_discovers_merge() -> None:
    spec = get_tool("merge")

    assert spec.name == "merge"
    assert spec.supports(Engine.LOCAL)
    assert not spec.supports(Engine.CLOUD), "pypdf-only tools have no cloud engine"
    assert spec.accepts_multiple_inputs
    assert spec.default_suffix == ".pdf"


def test_the_registry_loads_the_local_strategy() -> None:
    assert isinstance(get_tool("merge").load_strategy(Engine.LOCAL), MergeLocal)


def test_the_router_runs_merge_end_to_end(two_pdfs: tuple[Path, Path], tmp_path: Path) -> None:
    """The whole path: registry → router → strategy → ToolResult."""
    a, b = two_pdfs
    router = EngineRouter(config=Config())
    sources = docs(a, b)
    target = router.target_for("merge", sources, requested=str(tmp_path / "merged.pdf"))

    result = router.run("merge", sources, target)

    assert result.engine_used is Engine.LOCAL
    assert page_count(target.destination) == 5
    assert result.details["pages"] == 5


def test_merge_cannot_derive_a_destination_and_says_so(
    two_pdfs: tuple[Path, Path],
) -> None:
    """`merge` always needs an explicit `-o`, and the refusal is the good outcome.

    The derived name comes from the first input plus the tool's `default_suffix`
    — and for `merge` those are both `.pdf`, so the derivation lands exactly on
    the first input every time. `OutputTarget.resolve` catches it, which is the
    single most destructive bug class in v2 being caught by construction rather
    than by anyone remembering.

    The consequence for the CLI is that `merge` must require `-o`; there is no
    sensible default name to invent. Recorded here so the next person meets it
    as a documented property rather than a puzzling error.
    """
    from docmax.core.errors import InPlaceOverwriteError

    a, b = two_pdfs
    router = EngineRouter(config=Config())

    with pytest.raises(InPlaceOverwriteError):
        router.target_for("merge", docs(a, b), force=True)


def test_the_tools_default_suffix_drives_the_derivation(tmp_path: Path) -> None:
    """`.pdf` comes from `merge`'s own ToolSpec, not from the caller.

    Visible only when the first input has a different extension — which for
    `merge` means an input it would go on to reject, so this exercises the
    router's use of the spec rather than a realistic merge.
    """
    source = tmp_path / "scan.tiff"
    source.write_bytes(b"x")
    router = EngineRouter(config=Config())

    target = router.target_for("merge", docs(source), force=True)

    assert target.destination == tmp_path / "scan.pdf"


def test_the_router_refuses_a_cloud_engine_for_merge(
    two_pdfs: tuple[Path, Path],
) -> None:
    """Uploading a document for a millisecond-long pure-Python operation is worse."""
    from docmax.core.errors import EngineNotSupportedError

    router = EngineRouter(config=Config())

    with pytest.raises(EngineNotSupportedError):
        router.resolve("merge", requested=Engine.CLOUD)


def test_the_router_reports_a_dry_run_without_writing(
    two_pdfs: tuple[Path, Path], tmp_path: Path
) -> None:
    a, b = two_pdfs
    out = tmp_path / "merged.pdf"
    router = EngineRouter(config=Config())

    result = router.run("merge", docs(a, b), target_at(out), dry_run=True)

    assert result.details["dry_run"] is True
    assert not out.exists()
