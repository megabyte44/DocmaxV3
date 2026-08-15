"""The atomic writers, tested against the failures they exist to prevent.

Each of the three v2 bugs in ADR 0003 gets a test that reproduces the situation
and asserts the new outcome, because "we write atomically now" is a claim about
what happens when something goes *wrong* — nothing about the happy path
demonstrates it.

The recurring assertion is that no staged file survives a failure. A leftover
temp is not merely untidy: these are created beside the destination, so a leak
puts debris in the user's own directory.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from docmax.core.atomic import atomic_dir, atomic_path, atomic_write
from docmax.core.errors import OutputValidationError
from docmax.core.models import OutputTarget


def target(destination: Path) -> OutputTarget:
    """A target built directly, bypassing ``resolve``.

    ``OutputTarget.resolve`` is covered in ``test_models.py``; these tests are
    about what happens *after* a destination has been approved.
    """
    return OutputTarget(destination=destination, force=True)


def staged_files(directory: Path) -> list[str]:
    """Anything the writers left behind. Staged names all begin with a dot."""
    return sorted(path.name for path in directory.glob(".*"))


class Boom(Exception):
    """Raised inside a context manager to simulate a mid-operation failure."""


# ---------------------------------------------------------------------------
# atomic_write — output this process produces
# ---------------------------------------------------------------------------


def test_atomic_write_delivers_the_bytes(tmp_path: Path) -> None:
    destination = tmp_path / "out.pdf"

    with atomic_write(target(destination)) as handle:
        handle.write(b"content")

    assert destination.read_bytes() == b"content"
    assert not staged_files(tmp_path)


@pytest.mark.parametrize("failure", [Boom, KeyboardInterrupt])
def test_a_crash_mid_write_leaves_the_previous_file(
    tmp_path: Path, failure: type[BaseException]
) -> None:
    """v2 left a truncated file here. Half a PDF is not a PDF.

    ``KeyboardInterrupt`` is parametrised in deliberately: it does not inherit
    from ``Exception``, so cleanup written as ``except Exception`` would pass the
    first case and leak on the second — which is the more common one in a
    terminal tool.
    """
    destination = tmp_path / "out.pdf"
    destination.write_bytes(b"the original")

    with pytest.raises(failure), atomic_write(target(destination)) as handle:
        handle.write(b"a partial write that must never land")
        raise failure

    assert destination.read_bytes() == b"the original"
    assert not staged_files(tmp_path)


def test_a_crash_before_any_output_creates_nothing(tmp_path: Path) -> None:
    destination = tmp_path / "absent.pdf"

    with pytest.raises(Boom), atomic_write(target(destination)) as handle:
        handle.write(b"x")
        raise Boom

    assert not destination.exists()
    assert not staged_files(tmp_path)


def test_atomic_write_replaces_an_existing_file(tmp_path: Path) -> None:
    destination = tmp_path / "out.pdf"
    destination.write_bytes(b"old")

    with atomic_write(target(destination)) as handle:
        handle.write(b"new")

    assert destination.read_bytes() == b"new"


# ---------------------------------------------------------------------------
# Validators — the check that runs while the destination is still untouched
# ---------------------------------------------------------------------------


def test_a_failing_validator_prevents_delivery(tmp_path: Path) -> None:
    """A wrong result is never delivered at all, rather than delivered and noted."""
    destination = tmp_path / "out.pdf"
    destination.write_bytes(b"the original")

    def reject(produced: Path) -> None:
        raise OutputValidationError("wrong page count")

    with (
        pytest.raises(OutputValidationError),
        atomic_write(target(destination), validators=(reject,)) as handle,
    ):
        handle.write(b"plausible but wrong")

    assert destination.read_bytes() == b"the original"
    assert not staged_files(tmp_path)


def test_a_validator_inspects_the_staged_file_not_the_destination(tmp_path: Path) -> None:
    destination = tmp_path / "out.pdf"
    seen: list[tuple[Path, int]] = []

    def record(produced: Path) -> None:
        seen.append((produced, produced.stat().st_size))

    with atomic_write(target(destination), validators=(record,)) as handle:
        handle.write(b"1234")

    path, size = seen[0]
    assert size == 4
    assert path != destination, "the validator must run before the swap, not after"


def test_an_unexpected_validator_error_becomes_a_typed_one(tmp_path: Path) -> None:
    """A buggy validator is still a failed output, not a leaked ValueError."""
    destination = tmp_path / "out.pdf"

    def buggy(produced: Path) -> None:
        raise ValueError("the validator itself is broken")

    with (
        pytest.raises(OutputValidationError),
        atomic_write(target(destination), validators=(buggy,)) as handle,
    ):
        handle.write(b"x")

    assert not destination.exists()


# ---------------------------------------------------------------------------
# atomic_path — output an external program produces
# ---------------------------------------------------------------------------


def test_atomic_path_delivers_external_output(tmp_path: Path) -> None:
    destination = tmp_path / "compressed.pdf"

    with atomic_path(target(destination)) as staged:
        staged.write_bytes(b"as if by ghostscript")

    assert destination.read_bytes() == b"as if by ghostscript"
    assert not staged_files(tmp_path)


def test_atomic_path_tolerates_a_program_that_recreates_the_file(tmp_path: Path) -> None:
    """Plenty of tools unlink and rewrite rather than truncating in place."""
    destination = tmp_path / "out.pdf"

    with atomic_path(target(destination)) as staged:
        staged.unlink()
        staged.write_bytes(b"a fresh inode")

    assert destination.read_bytes() == b"a fresh inode"


def _write_nothing(staged: Path) -> None:
    """An external tool that exits zero having produced no output."""


def _delete_the_output(staged: Path) -> None:
    """An external tool that removes the file it was told to write."""
    staged.unlink()


@pytest.mark.parametrize(
    ("scenario", "action"),
    [
        ("wrote nothing", _write_nothing),
        ("deleted its own output", _delete_the_output),
    ],
)
def test_atomic_path_refuses_to_deliver_nothing(
    tmp_path: Path, scenario: str, action: Callable[[Path], None]
) -> None:
    """A silent no-op from an external tool must not read as success.

    Ghostscript can exit zero having produced nothing. Delivering an empty file
    would replace a real document with a valid-looking husk.
    """
    destination = tmp_path / "out.pdf"
    destination.write_bytes(b"the original")

    with pytest.raises(OutputValidationError), atomic_path(target(destination)) as staged:
        action(staged)

    assert destination.read_bytes() == b"the original", scenario
    assert not staged_files(tmp_path)


# ---------------------------------------------------------------------------
# atomic_dir — many outputs, delivered as one
# ---------------------------------------------------------------------------


def test_atomic_dir_delivers_a_whole_tree(tmp_path: Path) -> None:
    destination = tmp_path / "pages"

    with atomic_dir(target(destination)) as staged:
        (staged / "1.png").write_bytes(b"one")
        (staged / "2.png").write_bytes(b"two")

    assert sorted(path.name for path in destination.iterdir()) == ["1.png", "2.png"]
    assert not staged_files(tmp_path)


def test_atomic_dir_replaces_an_existing_tree_completely(tmp_path: Path) -> None:
    """The old contents go, rather than the new ones merging into them."""
    destination = tmp_path / "pages"
    destination.mkdir()
    (destination / "stale.png").write_bytes(b"from a previous run")

    with atomic_dir(target(destination)) as staged:
        (staged / "fresh.png").write_bytes(b"current")

    assert [path.name for path in destination.iterdir()] == ["fresh.png"]
    assert not staged_files(tmp_path)


def test_cancelling_a_multi_file_run_keeps_the_old_tree(tmp_path: Path) -> None:
    """``split`` on a thousand pages must not leave the first forty behind."""
    destination = tmp_path / "pages"
    destination.mkdir()
    (destination / "complete.png").write_bytes(b"from a finished run")

    with pytest.raises(Boom), atomic_dir(target(destination)) as staged:
        (staged / "partial.png").write_bytes(b"page 40 of 1000")
        raise Boom

    assert [path.name for path in destination.iterdir()] == ["complete.png"]
    assert not staged_files(tmp_path)


def test_a_cancelled_nested_tree_is_cleaned_up(tmp_path: Path) -> None:
    """Cleanup recurses, so a staged tree with subdirectories leaves nothing."""
    destination = tmp_path / "output"

    with pytest.raises(Boom), atomic_dir(target(destination)) as staged:
        (staged / "sub" / "deeper").mkdir(parents=True)
        (staged / "sub" / "deeper" / "leaf.txt").write_text("x", encoding="utf-8")
        raise Boom

    assert not destination.exists()
    assert not staged_files(tmp_path)
