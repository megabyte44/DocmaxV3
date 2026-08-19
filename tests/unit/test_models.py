"""The core value types, tested at the boundaries they defend.

Most of these assert a *refusal*. `OutputTarget.resolve` exists to make the
destructive cases of v2 unreachable, so the tests that matter are the ones where
it declines to produce a target at all.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from docmax.core.errors import (
    InPlaceOverwriteError,
    InputNotFoundError,
    InvalidParameterError,
    OutputExistsError,
    OutputNotWritableError,
)
from docmax.core.models import DocumentRef, Engine, OutputTarget, ToolResult


@pytest.fixture
def document(tmp_path: Path) -> DocumentRef:
    path = tmp_path / "input.pdf"
    path.write_bytes(b"%PDF-1.7\n")
    return DocumentRef.from_path(path)


# ---------------------------------------------------------------------------
# DocumentRef
# ---------------------------------------------------------------------------


def test_from_path_accepts_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"x")

    ref = DocumentRef.from_path(path)

    assert ref.path == path.resolve()
    assert ref.size_bytes == 1


def test_from_path_resolves_relative_paths(tmp_path: Path) -> None:
    """Downstream code compares paths for the in-place check; they must be absolute."""
    path = tmp_path / "a.pdf"
    path.write_bytes(b"x")

    ref = DocumentRef.from_path(path)

    assert ref.path.is_absolute()


def test_a_missing_file_fails_once_at_the_boundary(tmp_path: Path) -> None:
    """Not as an OSError from inside a parser, after a temp file already exists."""
    with pytest.raises(InputNotFoundError) as caught:
        DocumentRef.from_path(tmp_path / "absent.pdf")

    assert "absent.pdf" in str(caught.value)
    assert caught.value.remedy


def test_a_directory_is_not_a_document(tmp_path: Path) -> None:
    """`is_file`, not `exists` — a directory reaching a PDF parser reads far worse."""
    with pytest.raises(InputNotFoundError):
        DocumentRef.from_path(tmp_path)


def test_suffix_is_lowercased(tmp_path: Path) -> None:
    """Case is not a format. `.PDF` off a Windows share is still a PDF."""
    path = tmp_path / "SCAN.PDF"
    path.write_bytes(b"x")

    assert DocumentRef.from_path(path).suffix == ".pdf"


# ---------------------------------------------------------------------------
# OutputTarget — the refusals
# ---------------------------------------------------------------------------


def test_the_output_may_not_be_an_input(tmp_path: Path, document: DocumentRef) -> None:
    """v2's worst bug: `merge a.pdf b.pdf -o a.pdf` consumed one of its own inputs."""
    with pytest.raises(InPlaceOverwriteError) as caught:
        OutputTarget.resolve(inputs=[document], requested=document.path)

    assert caught.value.context["path"] == str(document.path)


def test_a_derived_destination_is_checked_too(tmp_path: Path) -> None:
    """`convert x.pdf --to pdf` derives its own input, and must still be refused.

    The in-place check runs after the derivation for exactly this reason — the
    dangerous path is the one the user never typed.
    """
    source = tmp_path / "x.pdf"
    source.write_bytes(b"x")
    document = DocumentRef.from_path(source)

    with pytest.raises(InPlaceOverwriteError):
        OutputTarget.resolve(inputs=[document], default_suffix=".pdf")


def test_an_existing_destination_needs_force(tmp_path: Path, document: DocumentRef) -> None:
    destination = tmp_path / "out.pdf"
    destination.write_bytes(b"a previous run")

    with pytest.raises(OutputExistsError) as caught:
        OutputTarget.resolve(inputs=[document], requested=destination)

    assert "--force" in (caught.value.remedy or "")


def test_force_permits_an_existing_destination(tmp_path: Path, document: DocumentRef) -> None:
    destination = tmp_path / "out.pdf"
    destination.write_bytes(b"a previous run")

    target = OutputTarget.resolve(inputs=[document], requested=destination, force=True)

    assert target.destination == destination
    assert target.force is True


def test_a_missing_output_directory_is_refused(tmp_path: Path, document: DocumentRef) -> None:
    """Caught here rather than after an expensive operation has already run."""
    with pytest.raises(OutputNotWritableError):
        OutputTarget.resolve(inputs=[document], requested=tmp_path / "nope" / "out.pdf")


def test_no_inputs_and_no_destination_is_a_parameter_error(tmp_path: Path) -> None:
    with pytest.raises(InvalidParameterError) as caught:
        OutputTarget.resolve(inputs=[])

    assert "-o" in (caught.value.remedy or "")


# ---------------------------------------------------------------------------
# OutputTarget — the successes
# ---------------------------------------------------------------------------


def test_an_explicit_destination_is_honoured(tmp_path: Path, document: DocumentRef) -> None:
    target = OutputTarget.resolve(inputs=[document], requested=tmp_path / "out.pdf")

    assert target.destination == tmp_path / "out.pdf"
    assert target.force is False


def test_a_destination_is_derived_from_the_first_input(tmp_path: Path) -> None:
    source = tmp_path / "scan.tiff"
    source.write_bytes(b"x")
    document = DocumentRef.from_path(source)

    target = OutputTarget.resolve(inputs=[document], default_suffix=".pdf")

    assert target.destination == tmp_path / "scan.pdf"


def test_only_the_first_input_drives_the_derivation(tmp_path: Path) -> None:
    """`merge a.pdf b.pdf` without -o names itself after `a`, not `b`."""
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    for path in (first, second):
        path.write_bytes(b"x")

    target = OutputTarget.resolve(
        inputs=[DocumentRef.from_path(first), DocumentRef.from_path(second)],
        default_suffix=".pdf",
    )

    assert target.destination == tmp_path / "a.pdf"


def _is_case_insensitive(directory: Path) -> bool:
    """Probe the actual filesystem rather than guessing from the platform.

    macOS is usually case-insensitive but can be formatted either way, and a
    Linux CI runner may mount one of each. Asking is cheap; assuming is wrong
    somewhere in a three-platform matrix.
    """
    probe = directory / "probe.tmp"
    probe.write_bytes(b"x")
    try:
        return (directory / "PROBE.TMP").exists()
    finally:
        probe.unlink()


def test_case_differing_paths_are_the_same_input_when_the_filesystem_says_so(
    tmp_path: Path,
) -> None:
    """`-o DOC.PDF` against an input of `doc.pdf` is in-place on Windows and macOS.

    This is the most destructive failure class in the project, and comparing the
    strings misses it. `Path.resolve()` returns the name as the filesystem
    stores it on Windows, so both sides normalise and a string comparison
    happens to work there — but on macOS `resolve()` leaves the case exactly as
    typed, so the two paths differ while naming one file.

    `OutputTarget.resolve` therefore asks the operating system, via `samefile`,
    rather than comparing paths. This test failed on all three macOS legs before
    it did.
    """
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"x")
    document = DocumentRef.from_path(source)
    shouting = tmp_path / "DOC.PDF"

    if _is_case_insensitive(tmp_path):
        with pytest.raises(InPlaceOverwriteError):
            OutputTarget.resolve(inputs=[document], requested=shouting, force=True)
    else:
        target = OutputTarget.resolve(inputs=[document], requested=shouting, force=True)
        assert target.destination.name == "DOC.PDF", "a distinct file on a case-sensitive fs"


def test_a_hard_link_to_an_input_is_still_that_input(tmp_path: Path) -> None:
    """Two different names, one file, no case-folding involved.

    The case test above only reaches the `samefile` check on a case-insensitive
    volume, so on Linux it proves nothing. A hard link is the same defect
    expressed in a way every filesystem understands: no amount of path
    normalisation would ever equate these two names.
    """
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"x")
    link = tmp_path / "same-file-other-name.pdf"
    try:
        link.hardlink_to(source)
    except (OSError, NotImplementedError):  # pragma: no cover - fs without links
        pytest.skip("this filesystem does not support hard links")

    with pytest.raises(InPlaceOverwriteError):
        OutputTarget.resolve(inputs=[DocumentRef.from_path(source)], requested=link, force=True)


def test_every_input_is_checked_not_only_the_first(tmp_path: Path) -> None:
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    for path in (first, second):
        path.write_bytes(b"x")

    with pytest.raises(InPlaceOverwriteError):
        OutputTarget.resolve(
            inputs=[DocumentRef.from_path(first), DocumentRef.from_path(second)],
            requested=second,
        )


# ---------------------------------------------------------------------------
# ToolResult and Engine
# ---------------------------------------------------------------------------


def test_engine_values_are_the_wire_strings() -> None:
    """These appear in config files, `--engine` arguments and JSON output."""
    assert Engine.LOCAL.value == "local"
    assert Engine.CLOUD.value == "cloud"
    assert Engine.AUTO.value == "auto"


def test_a_result_describes_which_engine_ran(tmp_path: Path) -> None:
    result = ToolResult(
        outputs=(tmp_path / "out.pdf",),
        engine_used=Engine.LOCAL,
        duration_ms=42,
        engine_version="pypdf/4.2.0",
        details={"pages": 10},
    )

    assert result.engine_used is Engine.LOCAL
    assert result.details["pages"] == 10


def test_results_do_not_share_a_details_mapping(tmp_path: Path) -> None:
    """A mutable default here would leak one operation's details into the next."""
    first = ToolResult(outputs=(), engine_used=Engine.LOCAL)
    second = ToolResult(outputs=(), engine_used=Engine.LOCAL)

    assert first.details is not second.details


def _frozen_cases(tmp_path: Path) -> list[tuple[object, str]]:
    return [
        (DocumentRef(path=tmp_path / "a.pdf"), "path"),
        (OutputTarget(destination=tmp_path / "out.pdf"), "force"),
        (ToolResult(outputs=(), engine_used=Engine.LOCAL), "engine_used"),
    ]


def test_the_models_are_frozen(tmp_path: Path) -> None:
    """They cross threads and layers; none may be edited in transit.

    A target mutated after `resolve` checked it would defeat the check entirely,
    which is the specific reason this matters rather than a general preference
    for immutability.
    """
    for instance, attribute in _frozen_cases(tmp_path):
        with pytest.raises(FrozenInstanceError):
            setattr(instance, attribute, None)
