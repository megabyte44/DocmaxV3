"""The M5 commands and `docmax formats`, through the real registry and router.

Integration tests, for the reason `test_cli_m2.py` gives: `test_cli_merge.py`
already pins the *negative* — that the CLI decides nothing — with fakes and a
structural check, and repeating that per command would test one thing many
times. What is worth checking here is that each command's arguments arrive
intact at a real tool and that a real file comes out the other side.

Three things get more attention than the M2 and M4 equivalents:

* **`-o` is mandatory on `convert`.** ADR 0011 chose that over making
  `ToolSpec.default_suffix` parameter-dependent, so the CLI is where the
  decision actually lives and where it has to be held.
* **`formats` must render the shared declaration and hold no list of its own.**
  The tests compare its output against `tools/_formats.py` rather than against
  expected text, so a hard-coded list in the renderer fails.
* **`UnsupportedFormatError` must name a command the CLI registers.** That
  remedy has promised `docmax formats` since M0 with nothing behind it. This is
  the check that would have caught the gap, and ADR 0010 names it as the
  enforcement.

`convert` and `to-images` need binaries that are not installed on most
machines, so the commands that must reach a *tool* use `from-images`, which is
pure Python. The two binary-backed commands are exercised here for their
argument handling, their refusals, and their help — everything the CLI layer
actually owns.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from docmax.cli.execution import EXIT_FAILURE
from docmax.cli.main import app
from docmax.core.config import Config
from docmax.core.router import EngineRouter
from docmax.tools import _formats

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

COMMANDS = ("convert", "to-images", "from-images")

#: `from-images` is pure Python (no binary), but Pillow and img2pdf are still
#: the optional `images` extra, not base dependencies. Matches the `crypto`
#: extra's pattern in test_m4_tools.py.
needs_images = pytest.mark.skipif(
    importlib.util.find_spec("PIL") is None or importlib.util.find_spec("img2pdf") is None,
    reason="the images extra is not installed",
)

#: Typer's own exit code for a usage error — a missing required option is
#: refused by argument parsing, before any DocMax code runs.
EXIT_USAGE = 2


def plain(text: str) -> str:
    return _ANSI.sub("", text)


def shown(result: Any) -> str:
    """Both streams: results go to stdout, diagnostics to stderr."""
    return plain(result.stdout) + plain(result.stderr)


def squashed(text: str) -> str:
    """Rich wraps table cells, so a name can be split across lines.

    Collapsing whitespace lets a test ask "is this word in the table" without
    depending on the terminal width the test happened to run at.
    """
    return re.sub(r"\s+", "", plain(text))


def write_pdf(path: Path, pages: int = 3) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def write_image(path: Path, size: tuple[int, int] = (60, 40), colour: str = "red") -> Path:
    from PIL import Image

    Image.new("RGB", size, colour).save(path)
    return path


def page_count(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


@pytest.fixture
def real_router(monkeypatch: pytest.MonkeyPatch) -> None:
    """The genuine registry, router and tools; only the config is kept off disk."""
    router = EngineRouter(config=Config(), consent=None)
    monkeypatch.setattr("docmax.cli.execution.build_router", lambda: router)


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    path = tmp_path / "notes.md"
    path.write_text("# Title\n\nSome words.\n", encoding="utf-8")
    return path


@pytest.fixture
def images(tmp_path: Path) -> list[Path]:
    return [
        write_image(tmp_path / "one.png", (60, 40), "red"),
        write_image(tmp_path / "two.jpg", (80, 30), "blue"),
    ]


# ---------------------------------------------------------------------------
# Surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", COMMANDS)
def test_every_m5_command_is_offered(command: str) -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert command in plain(result.stdout)


@pytest.mark.parametrize("command", COMMANDS)
def test_every_m5_command_has_help(command: str) -> None:
    result = runner.invoke(app, [command, "--help"])

    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# `convert` requires -o — ADR 0011
# ---------------------------------------------------------------------------


def test_convert_cannot_be_invoked_without_an_output(real_router: None, notes: Path) -> None:
    """The decision ADR 0011 made instead of a parameter-dependent suffix.

    Refused by argument parsing, so it costs a usage error rather than a run
    that gets as far as looking for Pandoc.
    """
    result = runner.invoke(app, ["convert", str(notes), "--to", "docx"])

    assert result.exit_code == EXIT_USAGE
    assert "Traceback" not in shown(result)


def test_convert_cannot_be_invoked_without_a_target_format(
    real_router: None, notes: Path, tmp_path: Path
) -> None:
    result = runner.invoke(app, ["convert", str(notes), "-o", str(tmp_path / "out.docx")])

    assert result.exit_code == EXIT_USAGE


def test_convert_help_lists_the_shared_format_vocabulary(real_router: None) -> None:
    """`--to`'s help comes from the declaration, not from a list typed into the CLI."""
    listed = squashed(runner.invoke(app, ["convert", "--help"]).stdout)

    for name in _formats.convertible_names():
        assert name in listed, f"{name} is declared convertible but absent from --help"


def test_convert_help_points_at_the_formats_command(real_router: None) -> None:
    assert "formats" in plain(runner.invoke(app, ["convert", "--help"]).stdout)


def test_to_images_help_lists_the_shared_image_vocabulary(real_router: None) -> None:
    listed = squashed(runner.invoke(app, ["to-images", "--help"]).stdout)

    for name in _formats.rasterisable_names():
        assert name in listed


# ---------------------------------------------------------------------------
# Output collision — the existing DocMax behaviour, held for the new commands
# ---------------------------------------------------------------------------


@needs_images
def test_from_images_refuses_an_existing_output_without_force(
    real_router: None, images: list[Path], tmp_path: Path
) -> None:
    out = tmp_path / "album.pdf"
    out.write_bytes(b"already here")

    result = runner.invoke(app, ["from-images", *map(str, images), "-o", str(out)])

    assert result.exit_code == EXIT_FAILURE
    assert "output.exists" in shown(result)
    assert out.read_bytes() == b"already here", "the existing file is untouched"


@needs_images
def test_from_images_overwrites_an_existing_output_with_force(
    real_router: None, images: list[Path], tmp_path: Path
) -> None:
    out = tmp_path / "album.pdf"
    out.write_bytes(b"already here")

    result = runner.invoke(app, ["from-images", *map(str, images), "-o", str(out), "--force"])

    assert result.exit_code == 0, shown(result)
    assert page_count(out) == 2


@needs_images
def test_from_images_refuses_to_write_over_one_of_its_images(
    real_router: None, images: list[Path]
) -> None:
    """`from-images *.png -o one.png` would destroy the image it was built from."""
    result = runner.invoke(app, ["from-images", *map(str, images), "-o", str(images[0]), "--force"])

    assert result.exit_code == EXIT_FAILURE
    assert "output.in_place_overwrite" in shown(result)
    assert images[0].stat().st_size > 0


def test_convert_refuses_an_existing_output_without_force(
    real_router: None, notes: Path, tmp_path: Path
) -> None:
    """Checked before the engine is resolved, so it fires without Pandoc installed."""
    out = tmp_path / "out.docx"
    out.write_bytes(b"already here")

    result = runner.invoke(app, ["convert", str(notes), "--to", "docx", "-o", str(out)])

    assert result.exit_code == EXIT_FAILURE
    assert "output.exists" in shown(result)
    assert out.read_bytes() == b"already here"


def test_convert_refuses_to_write_over_its_own_input(real_router: None, notes: Path) -> None:
    """The case ADR 0003 names: `convert x.md --to md` writing over x.md."""
    result = runner.invoke(app, ["convert", str(notes), "--to", "md", "-o", str(notes), "--force"])

    assert result.exit_code == EXIT_FAILURE
    assert "output.in_place_overwrite" in shown(result)
    assert notes.read_text(encoding="utf-8").startswith("# Title")


def test_to_images_extensionless_output_names_a_directory_not_pdf_pdf(tmp_path: Path) -> None:
    """Requirement 5, ADR 0031. Resolved directly through a real router
    rather than run end to end, since `to-images` needs Poppler — this file's
    own docstring says exactly that binary-backed commands here are exercised
    for argument handling, not execution."""
    from docmax.core.models import DocumentRef

    source = write_pdf(tmp_path / "doc.pdf", 2)
    router = EngineRouter(config=Config())

    target = router.target_for(
        "to-images", [DocumentRef.from_path(source)], requested=str(tmp_path / "pages")
    )

    assert target.destination == tmp_path / "pages"
    assert target.destination.suffix == ""


def test_to_images_refuses_an_existing_output_without_force(
    real_router: None, tmp_path: Path
) -> None:
    source = write_pdf(tmp_path / "doc.pdf", 2)
    out = tmp_path / "pages"
    out.mkdir()

    result = runner.invoke(app, ["to-images", str(source), "-o", str(out)])

    assert result.exit_code == EXIT_FAILURE
    assert "output.exists" in shown(result)


# ---------------------------------------------------------------------------
# from-images — the one M5 command that reaches a tool with no binary installed
# ---------------------------------------------------------------------------


@needs_images
def test_from_images_writes_a_pdf(real_router: None, images: list[Path], tmp_path: Path) -> None:
    out = tmp_path / "album.pdf"

    result = runner.invoke(app, ["from-images", *map(str, images), "-o", str(out)])

    assert result.exit_code == 0, shown(result)
    assert page_count(out) == 2


@needs_images
def test_from_images_reports_what_it_wrote(
    real_router: None, images: list[Path], tmp_path: Path
) -> None:
    out = tmp_path / "album.pdf"

    result = runner.invoke(app, ["from-images", *map(str, images), "-o", str(out)])

    assert "Wrote" in shown(result)
    assert "local engine" in shown(result)


@needs_images
def test_from_images_reports_a_file_that_is_not_an_image_without_a_traceback(
    real_router: None, tmp_path: Path
) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("not an image", encoding="utf-8")

    result = runner.invoke(app, ["from-images", str(notes), "-o", str(tmp_path / "out.pdf")])

    assert result.exit_code == EXIT_FAILURE
    assert "input.unsupported_format" in shown(result)
    assert "Traceback" not in shown(result)


@needs_images
def test_a_from_images_dry_run_writes_nothing(
    real_router: None, images: list[Path], tmp_path: Path
) -> None:
    out = tmp_path / "never.pdf"

    result = runner.invoke(app, ["from-images", *map(str, images), "-o", str(out), "--dry-run"])

    assert result.exit_code == 0, shown(result)
    assert "Dry run" in shown(result)
    assert not out.exists()


# ---------------------------------------------------------------------------
# `docmax formats` — ADR 0010
# ---------------------------------------------------------------------------


def test_the_formats_command_exists() -> None:
    """The command `UnsupportedFormatError` has told users to run since M0."""
    result = runner.invoke(app, ["formats"])

    assert result.exit_code == 0, shown(result)


def test_formats_is_offered_in_the_top_level_help() -> None:
    assert "formats" in plain(runner.invoke(app, ["--help"]).stdout)


def test_formats_renders_every_declared_document_format() -> None:
    """Compared against the declaration, not against expected text."""
    listed = squashed(runner.invoke(app, ["formats"]).stdout)

    for item in _formats.DOCUMENT_FORMATS:
        assert item.name in listed, f"{item.name} is declared but not rendered"


def test_formats_renders_every_declared_image_format() -> None:
    listed = squashed(runner.invoke(app, ["formats"]).stdout)

    for item in _formats.IMAGE_FORMATS:
        assert item.name in listed, f"{item.name} is declared but not rendered"


def test_formats_shows_the_formats_that_cannot_be_used_and_why() -> None:
    """ "Unknown format: pdf" teaches nothing. A row with a reason teaches the fix."""
    shown_text = plain(runner.invoke(app, ["formats"]).stdout)

    assert "pdf" in squashed(shown_text)
    assert "LaTeX" in shown_text, "the note explaining why PDF is unavailable"


def test_formats_distinguishes_reading_from_writing() -> None:
    """`txt` is write-only, and the table has to say so rather than imply both."""
    result = runner.invoke(app, ["formats"])

    assert result.exit_code == 0
    assert "yes" in plain(result.stdout)
    assert "no" in plain(result.stdout)


def test_formats_holds_no_vocabulary_of_its_own() -> None:
    """The renderer reads `_formats`; a list typed into it would fail this.

    Asserted structurally rather than by output, because a duplicated list would
    agree with the declaration on the day it was written and drift later — which
    is exactly the failure ADR 0010 exists to prevent.
    """
    import inspect

    from docmax.cli import render

    source = inspect.getsource(render.render_formats)

    for name in (*_formats.convertible_names(), *_formats.rasterisable_names()):
        assert f'"{name}"' not in source, f"{name} is hard-coded in the renderer"
        assert f"'{name}'" not in source


def test_formats_writes_nothing_and_needs_no_document(tmp_path: Path) -> None:
    """It answers a question about DocMax, not about any file."""
    before = sorted(p.name for p in tmp_path.iterdir())

    result = runner.invoke(app, ["formats"])

    assert result.exit_code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == before


# ---------------------------------------------------------------------------
# The remedy that promised a command — ADR 0010's enforcement
# ---------------------------------------------------------------------------


def _registered_command_names() -> set[str]:
    """Every command name the CLI actually offers.

    Typer types `callback` as optional, and a command registered without one has
    no name to derive — so it is skipped rather than assumed. In practice every
    command here has both; the branch exists so the check stays honest if one
    ever does not.
    """
    names: set[str] = set()
    for command in app.registered_commands:
        if command.name:
            names.add(command.name)
        elif command.callback is not None:
            names.add(command.callback.__name__)
    return names


def test_the_unsupported_format_remedy_names_a_command_the_cli_registers() -> None:
    """`UnsupportedFormatError` has promised `docmax formats` since M0.

    This is the check that would have caught it pointing at nothing, and ADR
    0010 names it as the enforcement. It reads the command out of the remedy
    rather than asserting the literal string, so rewording the message cannot
    make the test pass while the command is still missing.
    """
    from docmax.core.branding import CLI_NAME
    from docmax.core.errors import UnsupportedFormatError

    remedy = UnsupportedFormatError("anything").remedy
    assert remedy is not None

    named = re.findall(rf"`{re.escape(CLI_NAME)}\s+([a-z-]+)", remedy)
    assert named, f"the remedy names no command: {remedy!r}"
    assert set(named) <= _registered_command_names(), (
        f"the remedy points at {named}, which the CLI does not register"
    )


@needs_images
def test_an_unsupported_format_error_reaches_the_user_with_that_remedy(
    real_router: None, tmp_path: Path
) -> None:
    """End to end: the refusal a user actually sees carries the actionable line."""
    notes = tmp_path / "notes.txt"
    notes.write_text("not an image", encoding="utf-8")

    result = runner.invoke(app, ["from-images", str(notes), "-o", str(tmp_path / "out.pdf")])

    assert result.exit_code == EXIT_FAILURE
    assert "formats" in shown(result)


#: Commands a remedy may name before they exist, and the milestone that brings
#: them. An entry here is a promise with a date on it rather than a gap.
#:
#: `cloud` is `CloudAuthError`'s "run `docmax cloud login`". The cloud engines
#: land at M6, and until then a user who somehow reaches that error is told to
#: run something that is not there — which is the same defect `formats` had, and
#: is listed rather than hidden so it is removed with the milestone that fixes
#: it. Nothing else may be added here without the same justification.
PLANNED_COMMANDS = {"cloud": "M6 — cloud engines"}


def test_every_command_named_in_a_default_remedy_exists_or_is_scheduled() -> None:
    """Not just this one error: no remedy anywhere may point at a missing command.

    `UnsupportedFormatError` is the one M5 fixed, but the same mistake is
    available to every error class and the cost of covering all of them is one
    loop. A command that has not shipped yet must be in `PLANNED_COMMANDS` with
    its milestone, so "not written yet" and "quietly wrong" stay distinguishable.
    """
    from docmax.core import errors
    from docmax.core.branding import CLI_NAME

    registered = _registered_command_names()
    for name in errors.__all__:
        candidate = getattr(errors, name)
        remedy = getattr(candidate, "default_remedy", None)
        if not isinstance(remedy, str):
            continue
        for command in re.findall(rf"`{re.escape(CLI_NAME)}\s+([a-z-]+)", remedy):
            assert command in registered or command in PLANNED_COMMANDS, (
                f"{name}.default_remedy points at `{CLI_NAME} {command}`, which the "
                "CLI does not register and which no milestone claims"
            )


def test_no_planned_command_has_quietly_shipped() -> None:
    """The exemption list has to shrink, not linger.

    Once `cloud` exists, its entry is stale and this says so — otherwise the
    list becomes a place where checks go to be permanently disabled.
    """
    already_here = _registered_command_names() & set(PLANNED_COMMANDS)

    assert not already_here, f"{sorted(already_here)} now exist; remove them from PLANNED_COMMANDS"
