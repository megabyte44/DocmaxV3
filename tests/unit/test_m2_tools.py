"""The seven M2 tools, tested against the contract they all share.

Each tool gets its own section, but the shape is the same one `merge`
established: does it do the thing, does it refuse bad input with a *typed*
error, does progress and cancellation reach it, and does a failure leave the
destination untouched.

The last of those carries the most weight, and `split` carries it for everyone:
it is the first tool to produce many outputs, so it is the first real exercise
of `atomic_dir`'s promise that a cancelled multi-file run leaves no partial
directory.

Fixtures are generated with pypdf rather than committed, so nothing here depends
on a file anyone has to keep.
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
    UnsupportedFormatError,
)
from docmax.core.models import DocumentRef, Engine, OutputTarget
from docmax.core.protocols import NULL_PROGRESS
from docmax.core.registry import get_tool
from docmax.core.router import EngineRouter

if TYPE_CHECKING:
    from docmax.core.models import ToolResult

M2 = ("split", "rotate", "pages", "reorder", "metadata", "sanitize", "get-info")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_pdf(path: Path, pages: int = 3, **meta: str) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if meta:
        writer.add_metadata({f"/{k}": v for k, v in meta.items()})
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def write_active_pdf(path: Path) -> Path:
    """A PDF carrying every active-content structure `sanitize` claims to remove."""
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=200, height=200)

    # Reaching into pypdf's catalog on purpose: this fixture has to be
    # genuinely dirty, and pypdf offers no public way to add JavaScript.
    catalog = writer._root_object
    catalog[NameObject("/OpenAction")] = ArrayObject([])
    catalog[NameObject("/AcroForm")] = DictionaryObject()
    names = DictionaryObject()
    names[NameObject("/JavaScript")] = DictionaryObject()
    names[NameObject("/EmbeddedFiles")] = DictionaryObject()
    catalog[NameObject("/Names")] = names
    writer.pages[0][NameObject("/AA")] = DictionaryObject()
    writer.pages[0][NameObject("/Annots")] = ArrayObject([])

    with path.open("wb") as handle:
        writer.write(handle)
    return path


def page_count(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def rotation(path: Path, index: int) -> int:
    from pypdf import PdfReader

    return int(PdfReader(str(path)).pages[index].get("/Rotate", 0) or 0)


def catalog_keys(path: Path) -> set[str]:
    """The document catalog's keys. pypdf types the trailer loosely, hence the cast."""
    from pypdf import PdfReader

    root: Any = PdfReader(str(path)).trailer["/Root"]
    return {str(key) for key in root}


def page_keys(path: Path, index: int = 0) -> set[str]:
    from pypdf import PdfReader

    return {str(key) for key in PdfReader(str(path)).pages[index]}


def staged(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.glob(".*"))


class RecordingProgress:
    def __init__(self) -> None:
        self.events: list[str] = []

    def start(self, description: str, *, total: int | None = None) -> None:
        self.events.append("start")

    def advance(self, amount: int = 1) -> None:
        self.events.append("advance")

    def finish(self) -> None:
        self.events.append("finish")


@pytest.fixture
def router() -> EngineRouter:
    """The real registry and router; only the config is kept off the real disk."""
    return EngineRouter(config=Config())


def run(
    router: EngineRouter,
    tool: str,
    source: Path,
    destination: Path,
    *,
    progress: Any = NULL_PROGRESS,
    cancellation: Any = NEVER_CANCELLED,
    **params: Any,
) -> ToolResult:
    return router.run(
        tool,
        [DocumentRef.from_path(source)],
        OutputTarget(destination=destination, force=True),
        progress=progress,
        cancellation=cancellation,
        **params,
    )


# ---------------------------------------------------------------------------
# Registry and router — every tool, uniformly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", M2)
def test_every_m2_tool_is_discoverable(name: str) -> None:
    spec = get_tool(name)

    assert spec.name == name
    assert spec.supports(Engine.LOCAL)


@pytest.mark.parametrize("name", M2)
def test_no_m2_tool_has_a_cloud_engine(name: str) -> None:
    """Pure pypdf: uploading for a millisecond-long local operation is strictly worse."""
    assert not get_tool(name).supports(Engine.CLOUD)


@pytest.mark.parametrize("name", M2)
def test_every_m2_tool_loads_its_strategy(name: str) -> None:
    strategy = get_tool(name).load_strategy(Engine.LOCAL)

    assert strategy.is_available() is True
    assert strategy.unavailable_reason() is None


@pytest.mark.parametrize("name", M2)
def test_every_m2_tool_refuses_a_non_pdf(router: EngineRouter, name: str, tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(UnsupportedFormatError):
        run(router, name, notes, tmp_path / "out", order="1", select="1")


@pytest.mark.parametrize("name", M2)
def test_every_m2_tool_refuses_a_corrupt_pdf(
    router: EngineRouter, name: str, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\ngarbage")

    with pytest.raises(CorruptDocumentError):
        run(router, name, broken, tmp_path / "out", order="1", select="1")


@pytest.mark.parametrize("name", ["split", "rotate", "pages", "reorder", "sanitize"])
def test_writing_tools_refuse_an_encrypted_pdf(
    router: EngineRouter, name: str, tmp_path: Path
) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    locked = tmp_path / "locked.pdf"
    with locked.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(EncryptedDocumentError):
        run(router, name, locked, tmp_path / "out", order="1", select="1")


@pytest.mark.parametrize("name", M2)
def test_an_already_cancelled_run_produces_nothing(
    router: EngineRouter, name: str, tmp_path: Path
) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 3)
    out = tmp_path / "out"
    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError):
        run(router, name, source, out, cancellation=token, order="1,2,3", select="1")

    assert not out.exists()


# ---------------------------------------------------------------------------
# split — the first real consumer of atomic_dir
# ---------------------------------------------------------------------------


def test_split_produces_one_file_per_page(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 3)
    out = tmp_path / "parts"

    result = run(router, "split", source, out)

    parts = sorted(out.glob("*.pdf"))
    assert len(parts) == 3
    assert result.details["files"] == 3
    assert all(page_count(part) == 1 for part in parts)


def test_split_groups_pages_with_every(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 5)
    out = tmp_path / "parts"

    result = run(router, "split", source, out, every=2)

    parts = sorted(out.glob("*.pdf"))
    assert [page_count(part) for part in parts] == [2, 2, 1], "the remainder is its own part"
    assert result.details["files"] == 3


def test_split_names_parts_in_reading_order(router: EngineRouter, tmp_path: Path) -> None:
    """Zero-padded, so a directory listing sorts the way the document reads."""
    source = write_pdf(tmp_path / "doc.pdf", 3)
    out = tmp_path / "parts"

    run(router, "split", source, out)

    assert [p.name for p in sorted(out.glob("*.pdf"))] == [
        "doc-0001.pdf",
        "doc-0002.pdf",
        "doc-0003.pdf",
    ]


def test_split_honours_a_page_selection(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 5)
    out = tmp_path / "parts"

    result = run(router, "split", source, out, pages="2-3")

    assert len(list(out.glob("*.pdf"))) == 2
    assert result.details["selection"] == "2-3"


def test_split_reports_every_part_it_wrote(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 3)
    out = tmp_path / "parts"

    result = run(router, "split", source, out)

    assert len(result.outputs) == 3
    assert all(path.parent == out for path in result.outputs)


@pytest.mark.parametrize("every", [0, -1, "two", 1.5])
def test_split_refuses_a_nonsense_every(router: EngineRouter, tmp_path: Path, every: Any) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 3)

    with pytest.raises(InvalidParameterError):
        run(router, "split", source, tmp_path / "parts", every=every)


def test_split_leaves_no_partial_directory_when_cancelled(
    router: EngineRouter, tmp_path: Path
) -> None:
    """The guarantee `atomic_dir` exists for, exercised by a real tool at last."""
    source = write_pdf(tmp_path / "doc.pdf", 6)
    out = tmp_path / "parts"
    token = CancellationToken()

    class CancelAfterFirstPart:
        def start(self, description: str, *, total: int | None = None) -> None: ...

        def advance(self, amount: int = 1) -> None:
            token.cancel()

        def finish(self) -> None: ...

    with pytest.raises(CancelledError):
        run(
            router,
            "split",
            source,
            out,
            progress=CancelAfterFirstPart(),
            cancellation=token,
        )

    assert not out.exists(), "no directory at all, not a directory holding part one"
    assert staged(tmp_path) == [], "and nothing staged beside it"


def test_split_replaces_an_existing_directory_wholesale(
    router: EngineRouter, tmp_path: Path
) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 2)
    out = tmp_path / "parts"
    out.mkdir()
    (out / "stale.pdf").write_bytes(b"from a previous run")

    run(router, "split", source, out)

    assert "stale.pdf" not in {p.name for p in out.iterdir()}
    assert len(list(out.glob("*.pdf"))) == 2


def test_a_failed_split_leaves_the_previous_directory(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 4)
    out = tmp_path / "parts"
    out.mkdir()
    (out / "previous.pdf").write_bytes(b"from a finished run")
    token = CancellationToken()

    class CancelImmediately:
        def start(self, description: str, *, total: int | None = None) -> None:
            token.cancel()

        def advance(self, amount: int = 1) -> None: ...

        def finish(self) -> None: ...

    with pytest.raises(CancelledError):
        run(router, "split", source, out, progress=CancelImmediately(), cancellation=token)

    assert [p.name for p in out.iterdir()] == ["previous.pdf"]


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_rotate_applies_quarter_turns(router: EngineRouter, tmp_path: Path, degrees: int) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 2)
    out = tmp_path / "out.pdf"

    result = run(router, "rotate", source, out, by=degrees)

    assert rotation(out, 0) == degrees
    assert result.details["degrees"] == degrees


@pytest.mark.parametrize(("given", "stored"), [(360, 0), (-90, 270), (450, 90)])
def test_rotate_normalises_the_angle(
    router: EngineRouter, tmp_path: Path, given: int, stored: int
) -> None:
    """pypdf stores whatever it is handed, so `--by 360` would write /Rotate 360.

    Normalising means 360 is the no-op it reads as, -90 is the 270 the user
    meant, and the result reports the angle actually applied rather than the one
    typed.
    """
    source = write_pdf(tmp_path / "doc.pdf", 1)
    out = tmp_path / "out.pdf"

    result = run(router, "rotate", source, out, by=given)

    assert rotation(out, 0) == stored
    assert result.details["degrees"] == stored


def test_rotate_touches_only_the_selected_pages(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 3)
    out = tmp_path / "out.pdf"

    result = run(router, "rotate", source, out, by=90, pages="2")

    assert rotation(out, 0) == 0
    assert rotation(out, 1) == 90
    assert rotation(out, 2) == 0
    assert result.details["rotated"] == 1


def test_rotate_defaults_to_every_page(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 3)
    out = tmp_path / "out.pdf"

    result = run(router, "rotate", source, out, by=90)

    assert result.details["rotated"] == 3
    assert all(rotation(out, i) == 90 for i in range(3))


def test_rotate_preserves_the_page_count(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 4)
    out = tmp_path / "out.pdf"

    run(router, "rotate", source, out, by=90)

    assert page_count(out) == 4


@pytest.mark.parametrize("degrees", [45, 1, "90", 91])
def test_rotate_refuses_anything_but_a_quarter_turn(
    router: EngineRouter, tmp_path: Path, degrees: Any
) -> None:
    """A PDF stores rotation as a quarter turn; 45 is not representable at all."""
    source = write_pdf(tmp_path / "doc.pdf", 1)

    with pytest.raises(InvalidParameterError) as caught:
        run(router, "rotate", source, tmp_path / "out.pdf", by=degrees)

    assert "90" in (caught.value.remedy or "")


def test_rotate_refuses_a_page_that_does_not_exist(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 2)

    with pytest.raises(InvalidParameterError):
        run(router, "rotate", source, tmp_path / "out.pdf", by=90, pages="9")


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------


def test_pages_keeps_a_selection(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 5)
    out = tmp_path / "out.pdf"

    result = run(router, "pages", source, out, select="1-2,5")

    assert page_count(out) == 3
    assert result.details["selection"] == "1-2,5"


def test_pages_deletes_a_selection(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 5)
    out = tmp_path / "out.pdf"

    result = run(router, "pages", source, out, delete="2,4")

    assert page_count(out) == 3
    assert result.details["removed"] == 2


@pytest.mark.parametrize("spec", ["1", "5", "3"])
def test_pages_handles_the_boundaries(router: EngineRouter, tmp_path: Path, spec: str) -> None:
    """First, last and middle — the three a range parser most often gets wrong."""
    source = write_pdf(tmp_path / "doc.pdf", 5)
    out = tmp_path / "out.pdf"

    run(router, "pages", source, out, select=spec)

    assert page_count(out) == 1


def test_pages_can_repeat_a_page(router: EngineRouter, tmp_path: Path) -> None:
    """Unlike reorder: extracting the same page twice is a real request."""
    source = write_pdf(tmp_path / "doc.pdf", 3)
    out = tmp_path / "out.pdf"

    run(router, "pages", source, out, select="1,1,1")

    assert page_count(out) == 3


def test_pages_refuses_both_select_and_delete(router: EngineRouter, tmp_path: Path) -> None:
    """Two ways of saying the same thing; a precedence would have to be invented."""
    source = write_pdf(tmp_path / "doc.pdf", 3)

    with pytest.raises(InvalidParameterError):
        run(router, "pages", source, tmp_path / "out.pdf", select="1", delete="2")


def test_pages_refuses_neither(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 3)

    with pytest.raises(InvalidParameterError):
        run(router, "pages", source, tmp_path / "out.pdf")


def test_pages_refuses_to_empty_the_document(router: EngineRouter, tmp_path: Path) -> None:
    """A zero-page PDF is not a document; the validators would reject it anyway."""
    source = write_pdf(tmp_path / "doc.pdf", 2)

    with pytest.raises(InvalidParameterError) as caught:
        run(router, "pages", source, tmp_path / "out.pdf", delete="1-2")

    assert "empty" in str(caught.value)


def test_pages_refuses_a_page_past_the_end(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 2)

    with pytest.raises(InvalidParameterError):
        run(router, "pages", source, tmp_path / "out.pdf", select="5")


# ---------------------------------------------------------------------------
# reorder
# ---------------------------------------------------------------------------


def test_reorder_reverses(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 3)
    out = tmp_path / "out.pdf"

    result = run(router, "reorder", source, out, order="3,2,1")

    assert page_count(out) == 3
    assert result.details["order"] == "3,2,1"


def test_reorder_swaps_two_pages(router: EngineRouter, tmp_path: Path) -> None:
    """Verified by rotation, which travels with the page it was applied to."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for degrees in (0, 90, 180):
        writer.add_blank_page(width=200, height=200)
        writer.pages[-1].rotate(degrees)
    source = tmp_path / "doc.pdf"
    with source.open("wb") as handle:
        writer.write(handle)

    out = tmp_path / "out.pdf"
    run(router, "reorder", source, out, order="2,1,3")

    pages = PdfReader(str(out)).pages
    assert [int(p.get("/Rotate", 0) or 0) for p in pages] == [90, 0, 180]


def test_reorder_refuses_a_missing_page(router: EngineRouter, tmp_path: Path) -> None:
    """A reorder that silently dropped a page would be discovered far too late."""
    source = write_pdf(tmp_path / "doc.pdf", 3)

    with pytest.raises(InvalidParameterError) as caught:
        run(router, "reorder", source, tmp_path / "out.pdf", order="1,2")

    assert "missing" in str(caught.value)
    assert caught.value.context["missing"] == [3]


def test_reorder_refuses_a_repeated_page(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 3)

    with pytest.raises(InvalidParameterError) as caught:
        run(router, "reorder", source, tmp_path / "out.pdf", order="1,1,2")

    assert "repeated" in str(caught.value)
    assert caught.value.context["repeated"] == [1]


def test_reorder_refuses_an_empty_order(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 2)

    with pytest.raises(InvalidParameterError):
        run(router, "reorder", source, tmp_path / "out.pdf", order="")


def test_reorder_accepts_the_identity(router: EngineRouter, tmp_path: Path) -> None:
    """Reordering into the existing order is a no-op, not an error."""
    source = write_pdf(tmp_path / "doc.pdf", 3)
    out = tmp_path / "out.pdf"

    run(router, "reorder", source, out, order="1,2,3")

    assert page_count(out) == 3


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------


def test_metadata_reads_without_writing(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 1, Title="Report", Author="Ada")
    out = tmp_path / "unused.pdf"

    result = run(router, "metadata", source, out)

    assert result.outputs == (), "reading produces no file"
    assert result.details["metadata"]["/Title"] == "Report"
    assert not out.exists()


def test_metadata_writes_a_field(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 2, Title="Old")
    out = tmp_path / "out.pdf"

    result = run(router, "metadata", source, out, set=["Title=New"])

    assert result.details["metadata"]["/Title"] == "New"
    assert page_count(out) == 2


def test_metadata_preserves_the_fields_it_was_not_asked_about(
    router: EngineRouter, tmp_path: Path
) -> None:
    """Setting a title must not silently erase the author."""
    source = write_pdf(tmp_path / "doc.pdf", 1, Title="Old", Author="Ada")
    out = tmp_path / "out.pdf"

    result = run(router, "metadata", source, out, set=["Title=New"])

    assert result.details["metadata"]["/Author"] == "Ada"


def test_metadata_clear_removes_everything_first(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 1, Title="Old", Author="Ada")
    out = tmp_path / "out.pdf"

    result = run(router, "metadata", source, out, set=["Title=Only"], clear=True)

    assert result.details["metadata"] == {"/Title": "Only"}


def test_metadata_accepts_several_fields(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 1)
    out = tmp_path / "out.pdf"

    result = run(router, "metadata", source, out, set=["Title=T", "Author=A", "Subject=S"])

    written = result.details["metadata"]
    assert (written["/Title"], written["/Author"], written["/Subject"]) == ("T", "A", "S")


def test_metadata_reads_an_empty_document(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 1)

    result = run(router, "metadata", source, tmp_path / "unused.pdf")

    assert isinstance(result.details["metadata"], dict)


def test_metadata_refuses_an_unknown_field(router: EngineRouter, tmp_path: Path) -> None:
    """`Titel=x` would otherwise write a field no reader ever shows."""
    source = write_pdf(tmp_path / "doc.pdf", 1)

    with pytest.raises(InvalidParameterError) as caught:
        run(router, "metadata", source, tmp_path / "out.pdf", set=["Titel=x"])

    assert "Title" in (caught.value.remedy or "")


def test_metadata_refuses_a_pair_without_an_equals(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 1)

    with pytest.raises(InvalidParameterError):
        run(router, "metadata", source, tmp_path / "out.pdf", set=["Title"])


def test_metadata_field_names_are_case_insensitive(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 1)
    out = tmp_path / "out.pdf"

    result = run(router, "metadata", source, out, set=["title=Lower"])

    assert result.details["metadata"]["/Title"] == "Lower"


# ---------------------------------------------------------------------------
# sanitize
# ---------------------------------------------------------------------------


def test_sanitize_removes_document_level_active_content(
    router: EngineRouter, tmp_path: Path
) -> None:
    """Exactly the catalog entries the docstring names, and nothing claimed beyond."""
    source = write_active_pdf(tmp_path / "dirty.pdf")
    out = tmp_path / "clean.pdf"

    before = catalog_keys(source)
    assert {"/Names", "/OpenAction", "/AcroForm"} <= before, "the fixture is genuinely dirty"

    run(router, "sanitize", source, out)

    after = catalog_keys(out)
    assert "/Names" not in after, "JavaScript and embedded files live here"
    assert "/OpenAction" not in after
    assert "/AcroForm" not in after


def test_sanitize_removes_page_level_actions(router: EngineRouter, tmp_path: Path) -> None:
    """`/AA` and `/Annots` survive a page rebuild, so they are deleted explicitly."""
    source = write_active_pdf(tmp_path / "dirty.pdf")
    out = tmp_path / "clean.pdf"

    assert {"/AA", "/Annots"} <= page_keys(source)

    result = run(router, "sanitize", source, out)

    assert "/AA" not in page_keys(out)
    assert "/Annots" not in page_keys(out)
    assert result.details["page_entries_removed"] == 2


def test_sanitize_preserves_the_pages(router: EngineRouter, tmp_path: Path) -> None:
    source = write_active_pdf(tmp_path / "dirty.pdf")
    out = tmp_path / "clean.pdf"

    run(router, "sanitize", source, out)

    assert page_count(out) == page_count(source) == 2


def test_sanitize_leaves_a_clean_document_readable(router: EngineRouter, tmp_path: Path) -> None:
    """A file with nothing to remove still comes out intact."""
    source = write_pdf(tmp_path / "clean-in.pdf", 3)
    out = tmp_path / "clean-out.pdf"

    result = run(router, "sanitize", source, out)

    assert page_count(out) == 3
    assert result.details["page_entries_removed"] == 0


# ---------------------------------------------------------------------------
# get-info
# ---------------------------------------------------------------------------


def test_get_info_reports_the_basics(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 4, Title="Report")

    result = run(router, "get-info", source, tmp_path / "unused")

    details = result.details
    assert details["pages"] == 4
    assert details["encrypted"] is False
    assert details["size_bytes"] == source.stat().st_size
    assert details["metadata"]["/Title"] == "Report"


def test_get_info_writes_nothing(router: EngineRouter, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 1)
    out = tmp_path / "unused"

    result = run(router, "get-info", source, out)

    assert result.outputs == ()
    assert not out.exists()


def test_get_info_reports_encryption_rather_than_refusing(
    router: EngineRouter, tmp_path: Path
) -> None:
    """ "Is this locked?" is exactly the question someone runs this to answer."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    locked = tmp_path / "locked.pdf"
    with locked.open("wb") as handle:
        writer.write(handle)

    result = run(router, "get-info", locked, tmp_path / "unused")

    assert result.details["encrypted"] is True
    assert result.details["pages"] is None, "unknown, not zero — the tree is unreadable"


# ---------------------------------------------------------------------------
# Progress, results and atomic output — across the writing tools
# ---------------------------------------------------------------------------


WRITING = ("rotate", "pages", "reorder", "sanitize")


@pytest.mark.parametrize("name", WRITING)
def test_progress_is_started_and_advanced(router: EngineRouter, name: str, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 3)
    progress = RecordingProgress()

    run(
        router,
        name,
        source,
        tmp_path / "out.pdf",
        progress=progress,
        order="1,2,3",
        select="1-3",
    )

    assert progress.events[0] == "start"
    assert progress.events.count("advance") == 3


@pytest.mark.parametrize("name", WRITING)
def test_the_progress_region_is_closed_exactly_once(
    router: EngineRouter, name: str, tmp_path: Path
) -> None:
    """The router closes it in a `finally`; a tool that also did would double-finish.

    Run through the router, so this checks the pair rather than the tool alone:
    the region opens once and closes once, however the two divide the work.
    """
    source = write_pdf(tmp_path / "doc.pdf", 2)
    progress = RecordingProgress()

    run(
        router,
        name,
        source,
        tmp_path / "out.pdf",
        progress=progress,
        order="1,2",
        select="1-2",
    )

    assert progress.events.count("start") == 1
    assert progress.events.count("finish") == 1, "the router closed it, and only it"


@pytest.mark.parametrize("name", WRITING)
def test_the_result_describes_the_operation(
    router: EngineRouter, name: str, tmp_path: Path
) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 2)
    out = tmp_path / "out.pdf"

    result = run(router, name, source, out, order="1,2", select="1-2")

    assert result.outputs == (out,)
    assert result.engine_used is Engine.LOCAL
    assert result.engine_version is not None
    assert result.engine_version.startswith("pypdf/")
    assert result.details["pages"] == 2


@pytest.mark.parametrize("name", WRITING)
def test_an_existing_destination_survives_a_failure(
    router: EngineRouter, name: str, tmp_path: Path
) -> None:
    """The v2 bug this project was rebuilt to make unreachable."""
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\ngarbage")
    out = tmp_path / "out.pdf"
    out.write_bytes(b"a previous run's output")

    with pytest.raises(CorruptDocumentError):
        run(router, name, broken, out, order="1", select="1")

    assert out.read_bytes() == b"a previous run's output"
    assert staged(tmp_path) == []


@pytest.mark.parametrize("name", WRITING)
def test_no_staged_file_survives_success(router: EngineRouter, name: str, tmp_path: Path) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 2)

    run(router, name, source, tmp_path / "out.pdf", order="1,2", select="1-2")

    assert staged(tmp_path) == []
