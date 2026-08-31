"""The TUI's remembered last-used folder (issue #29).

One property carries most of the weight, the same one ``core/consent.py`` is
tested against: **it fails closed, harmlessly.** A missing, unreadable,
corrupt, future-schema, or now-vanished directory all mean "nothing to
offer" rather than an error — the caller already has a well-defined fallback,
``tui/browser.py``'s ``default_start()``, and does not need to be told why
the shortcut was unavailable this time.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docmax.core.ui_state import SCHEMA_VERSION, load_last_directory, save_last_directory


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "ui_state.json"


# ---------------------------------------------------------------------------
# Reading and writing
# ---------------------------------------------------------------------------


def test_nothing_is_remembered_by_default(path: Path) -> None:
    assert load_last_directory(path) is None


def test_a_saved_directory_round_trips(path: Path, tmp_path: Path) -> None:
    folder = tmp_path / "documents"
    folder.mkdir()

    save_last_directory(path, folder)

    assert load_last_directory(path) == folder


def test_the_record_survives_being_read_again(path: Path, tmp_path: Path) -> None:
    """It is a file, not a session — a later, independent call must see it."""
    folder = tmp_path / "documents"
    folder.mkdir()
    save_last_directory(path, folder)

    assert load_last_directory(path) == folder
    assert load_last_directory(path) == folder


def test_saving_again_replaces_the_previous_folder(path: Path, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    save_last_directory(path, first)
    save_last_directory(path, second)

    assert load_last_directory(path) == second


def test_the_directory_is_created_on_first_write(tmp_path: Path) -> None:
    """Nothing exists until something is remembered — no import side effects."""
    nested = tmp_path / "config" / "docmax-like" / "ui_state.json"
    folder = tmp_path / "documents"
    folder.mkdir()
    assert not nested.parent.exists()

    save_last_directory(nested, folder)

    assert nested.is_file()


def test_the_file_is_readable_by_a_human(path: Path, tmp_path: Path) -> None:
    folder = tmp_path / "documents"
    folder.mkdir()

    save_last_directory(path, folder)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["version"] == SCHEMA_VERSION
    assert document["last_directory"] == str(folder)


def test_a_partial_write_cannot_corrupt_the_record(path: Path, tmp_path: Path) -> None:
    """Writes go through core.atomic, so no staged file is left beside it."""
    folder = tmp_path / "documents"
    folder.mkdir()

    save_last_directory(path, folder)

    leftovers = [p.name for p in path.parent.glob(".*")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------


def test_a_missing_file_means_nothing_remembered(tmp_path: Path) -> None:
    assert load_last_directory(tmp_path / "nope.json") is None


@pytest.mark.parametrize(
    ("scenario", "body"),
    [
        ("not json", "this is not json at all"),
        ("json but not an object", "[1, 2, 3]"),
        ("no version", '{"last_directory": "/tmp"}'),
        ("last_directory missing", '{"version": 1}'),
        ("last_directory is not a string", '{"version": 1, "last_directory": 3}'),
        ("last_directory is empty", '{"version": 1, "last_directory": ""}'),
        ("empty file", ""),
    ],
)
def test_a_corrupt_record_means_nothing_remembered(path: Path, scenario: str, body: str) -> None:
    path.write_text(body, encoding="utf-8")

    assert load_last_directory(path) is None, scenario


def test_a_schema_from_the_future_means_nothing_remembered(path: Path, tmp_path: Path) -> None:
    """We cannot interpret it, and must not guess in the permissive direction."""
    folder = tmp_path / "documents"
    folder.mkdir()
    path.write_text(
        json.dumps({"version": SCHEMA_VERSION + 1, "last_directory": str(folder)}),
        encoding="utf-8",
    )

    assert load_last_directory(path) is None


def test_a_directory_where_the_file_should_be_means_nothing_remembered(tmp_path: Path) -> None:
    blocked = tmp_path / "ui_state.json"
    blocked.mkdir()

    assert load_last_directory(blocked) is None


def test_a_remembered_directory_that_no_longer_exists_is_not_offered(
    path: Path, tmp_path: Path
) -> None:
    """A moved or deleted folder (or an unplugged drive) must not be handed
    back as somewhere the dialog can actually open."""
    folder = tmp_path / "documents"
    folder.mkdir()
    save_last_directory(path, folder)
    folder.rmdir()

    assert load_last_directory(path) is None


def test_a_remembered_file_rather_than_a_directory_is_not_offered(
    path: Path, tmp_path: Path
) -> None:
    not_a_directory = tmp_path / "file.pdf"
    not_a_directory.write_bytes(b"%PDF-1.4")
    path.write_text(
        json.dumps({"version": SCHEMA_VERSION, "last_directory": str(not_a_directory)}),
        encoding="utf-8",
    )

    assert load_last_directory(path) is None
