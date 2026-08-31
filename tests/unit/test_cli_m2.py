"""The M2 commands, end to end through the real registry, router and tools.

These are integration tests on purpose. `test_cli_merge.py` already pins the
*negative* — that the CLI decides nothing — with fakes and a structural check;
repeating that seven times would test the same thing seven times. What is worth
checking here is that each command's arguments arrive intact at a real tool and
that a real file comes out the other side.

The router is injected only to keep `Config()` off the developer's real config
file. Everything below it is the genuine thing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from docmax.cli.execution import EXIT_FAILURE
from docmax.cli.main import app
from docmax.core.config import Config
from docmax.core.router import EngineRouter

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return _ANSI.sub("", text)


def shown(result: Any) -> str:
    """Both streams: results go to stdout, diagnostics to stderr."""
    return plain(result.stdout) + plain(result.stderr)


def write_pdf(path: Path, pages: int = 4, **meta: str) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if meta:
        writer.add_metadata({f"/{k}": v for k, v in meta.items()})
    with path.open("wb") as handle:
        writer.write(handle)
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
def source(tmp_path: Path) -> Path:
    return write_pdf(tmp_path / "doc.pdf", 4, Title="Original", Author="Ada")


COMMANDS = ("split", "rotate", "pages", "reorder", "metadata", "sanitize", "get-info")


# ---------------------------------------------------------------------------
# Surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", COMMANDS)
def test_every_m2_command_is_offered(command: str) -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert command in plain(result.stdout)


@pytest.mark.parametrize("command", COMMANDS)
def test_every_m2_command_has_help(command: str) -> None:
    result = runner.invoke(app, [command, "--help"])

    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------


def test_split_writes_a_directory_of_parts(real_router: None, source: Path, tmp_path: Path) -> None:
    out = tmp_path / "parts"

    result = runner.invoke(app, ["split", str(source), "-o", str(out)])

    assert result.exit_code == 0, shown(result)
    assert len(list(out.glob("*.pdf"))) == 4


def test_an_extensionless_output_names_a_directory_not_pdf_pdf(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    """Requirement 4, ADR 0031: `-o parts` with no extension names the
    directory `parts` — not `parts.pdf`, which is what a file-shaped
    normalisation would have produced (and did, before the fix)."""
    out = tmp_path / "parts"

    result = runner.invoke(app, ["split", str(source), "-o", str(out)])

    assert result.exit_code == 0, shown(result)
    assert out.is_dir()
    assert not (tmp_path / "parts.pdf").exists()
    assert len(list(out.glob("*.pdf"))) == 4


def test_split_honours_every(real_router: None, source: Path, tmp_path: Path) -> None:
    out = tmp_path / "parts"

    result = runner.invoke(app, ["split", str(source), "-o", str(out), "--every", "2"])

    assert result.exit_code == 0, shown(result)
    assert len(list(out.glob("*.pdf"))) == 2


def test_split_honours_a_page_selection(real_router: None, source: Path, tmp_path: Path) -> None:
    out = tmp_path / "parts"

    result = runner.invoke(app, ["split", str(source), "-o", str(out), "--pages", "1-2"])

    assert result.exit_code == 0, shown(result)
    assert len(list(out.glob("*.pdf"))) == 2


def test_split_requires_an_output(real_router: None, source: Path) -> None:
    result = runner.invoke(app, ["split", str(source)])

    assert result.exit_code == 2
    assert "--output" in shown(result)


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------


def test_rotate_writes_a_rotated_document(real_router: None, source: Path, tmp_path: Path) -> None:
    from pypdf import PdfReader

    out = tmp_path / "rot.pdf"

    result = runner.invoke(app, ["rotate", str(source), "-o", str(out), "--by", "180"])

    assert result.exit_code == 0, shown(result)
    assert PdfReader(str(out)).pages[0].get("/Rotate") == 180


def test_rotate_honours_a_page_selection(real_router: None, source: Path, tmp_path: Path) -> None:
    from pypdf import PdfReader

    out = tmp_path / "rot.pdf"

    result = runner.invoke(
        app, ["rotate", str(source), "-o", str(out), "--by", "90", "--pages", "2"]
    )

    assert result.exit_code == 0, shown(result)
    pages = PdfReader(str(out)).pages
    assert pages[0].get("/Rotate", 0) in (0, None)
    assert pages[1].get("/Rotate") == 90


def test_rotate_reports_a_bad_angle_without_a_traceback(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    result = runner.invoke(
        app, ["rotate", str(source), "-o", str(tmp_path / "x.pdf"), "--by", "45"]
    )

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "input.invalid_parameter" in text
    assert "Traceback" not in text


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------


def test_pages_keeps_a_selection(real_router: None, source: Path, tmp_path: Path) -> None:
    out = tmp_path / "sel.pdf"

    result = runner.invoke(app, ["pages", str(source), "-o", str(out), "--select", "1-2"])

    assert result.exit_code == 0, shown(result)
    assert page_count(out) == 2


def test_pages_deletes_a_selection(real_router: None, source: Path, tmp_path: Path) -> None:
    out = tmp_path / "del.pdf"

    result = runner.invoke(app, ["pages", str(source), "-o", str(out), "--delete", "1"])

    assert result.exit_code == 0, shown(result)
    assert page_count(out) == 3


def test_pages_refuses_both_at_once(real_router: None, source: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["pages", str(source), "-o", str(tmp_path / "x.pdf"), "--select", "1", "--delete", "2"],
    )

    assert result.exit_code == EXIT_FAILURE
    assert "not both" in shown(result)


def test_pages_refuses_a_bad_range(real_router: None, source: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["pages", str(source), "-o", str(tmp_path / "x.pdf"), "--select", "1-a"]
    )

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "not a page number" in text
    assert "Traceback" not in text


# ---------------------------------------------------------------------------
# reorder
# ---------------------------------------------------------------------------


def test_reorder_writes_a_reordered_document(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "reo.pdf"

    result = runner.invoke(app, ["reorder", str(source), "-o", str(out), "--order", "4,3,2,1"])

    assert result.exit_code == 0, shown(result)
    assert page_count(out) == 4


def test_reorder_requires_the_order(real_router: None, source: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["reorder", str(source), "-o", str(tmp_path / "x.pdf")])

    assert result.exit_code == 2
    assert "--order" in shown(result)


def test_reorder_refuses_an_incomplete_permutation(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    result = runner.invoke(
        app, ["reorder", str(source), "-o", str(tmp_path / "x.pdf"), "--order", "1,2"]
    )

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "missing" in text
    assert "Traceback" not in text


# ---------------------------------------------------------------------------
# sanitize
# ---------------------------------------------------------------------------


def test_sanitize_writes_a_clean_document(real_router: None, source: Path, tmp_path: Path) -> None:
    out = tmp_path / "san.pdf"

    result = runner.invoke(app, ["sanitize", str(source), "-o", str(out)])

    assert result.exit_code == 0, shown(result)
    assert page_count(out) == 4


def test_sanitize_requires_an_output(real_router: None, source: Path) -> None:
    result = runner.invoke(app, ["sanitize", str(source)])

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------


def test_metadata_reads_as_a_table(real_router: None, source: Path) -> None:
    result = runner.invoke(app, ["metadata", str(source)])

    text = shown(result)
    assert result.exit_code == 0, text
    assert "Original" in text
    assert "Ada" in text


def test_metadata_writes_a_field(real_router: None, source: Path, tmp_path: Path) -> None:
    from pypdf import PdfReader

    out = tmp_path / "meta.pdf"

    result = runner.invoke(app, ["metadata", str(source), "--set", "Title=Renamed", "-o", str(out)])

    assert result.exit_code == 0, shown(result)
    assert PdfReader(str(out)).metadata["/Title"] == "Renamed"  # type: ignore[index]


def test_metadata_accepts_set_more_than_once(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    """Repeatable rather than comma-separated, so a value may contain a comma."""
    from pypdf import PdfReader

    out = tmp_path / "meta.pdf"

    result = runner.invoke(
        app,
        [
            "metadata",
            str(source),
            "--set",
            "Title=A, with comma",
            "--set",
            "Author=B",
            "-o",
            str(out),
        ],
    )

    assert result.exit_code == 0, shown(result)
    written = PdfReader(str(out)).metadata
    assert written["/Title"] == "A, with comma"  # type: ignore[index]
    assert written["/Author"] == "B"  # type: ignore[index]


def test_metadata_requires_an_output_when_writing(real_router: None, source: Path) -> None:
    """Setting metadata produces a new document; this tool will not edit in place."""
    result = runner.invoke(app, ["metadata", str(source), "--set", "Title=X"])

    assert result.exit_code == 2
    assert "-o is required" in shown(result)


def test_metadata_rejects_an_output_when_reading(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    """An unused -o means the user expected something this command will not do."""
    result = runner.invoke(app, ["metadata", str(source), "-o", str(tmp_path / "x.pdf")])

    assert result.exit_code == 2
    assert "only used with" in shown(result)


def test_metadata_requires_an_output_because_the_spec_says_so(
    real_router: None, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0033: the refusal traces back to `ToolSpec.output_required`, not an
    assumption baked into `cli/commands.py` independently of it.

    `get_tool` is patched to hand back a `metadata` spec with
    `output_required=False`; if `cli/commands.py` still raised, that would
    mean the check had stopped reading the spec at all.
    """
    import dataclasses

    from docmax.core import registry as registry_module

    real_get_tool = registry_module.get_tool

    def fake_get_tool(name: str) -> Any:
        spec = real_get_tool(name)
        return dataclasses.replace(spec, output_required=False) if name == "metadata" else spec

    monkeypatch.setattr(registry_module, "get_tool", fake_get_tool)

    result = runner.invoke(app, ["metadata", str(source), "--set", "Title=X"])

    assert result.exit_code != 2, shown(result)
    assert "-o is required" not in shown(result)


def test_metadata_reports_an_unknown_field(real_router: None, source: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["metadata", str(source), "--set", "Titel=x", "-o", str(tmp_path / "x.pdf")]
    )

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "Traceback" not in text


# ---------------------------------------------------------------------------
# get-info
# ---------------------------------------------------------------------------


def test_get_info_reports_the_document(real_router: None, source: Path) -> None:
    result = runner.invoke(app, ["get-info", str(source)])

    text = shown(result)
    assert result.exit_code == 0, text
    assert "Pages" in text
    assert "4" in text
    assert "Encrypted" in text


def test_get_info_takes_no_output_option(real_router: None, source: Path, tmp_path: Path) -> None:
    """Read-only: there is nothing to write, so there is nothing to point at."""
    result = runner.invoke(app, ["get-info", str(source), "-o", str(tmp_path / "x")])

    assert result.exit_code == 2


def test_get_info_writes_nothing(real_router: None, source: Path, tmp_path: Path) -> None:
    before = {p.name for p in tmp_path.iterdir()}

    runner.invoke(app, ["get-info", str(source)])

    assert {p.name for p in tmp_path.iterdir()} == before


def test_get_info_reports_a_missing_file_cleanly(real_router: None, tmp_path: Path) -> None:
    result = runner.invoke(app, ["get-info", str(tmp_path / "absent.pdf")])

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "input.not_found" in text
    assert "Traceback" not in text


# ---------------------------------------------------------------------------
# Shared behaviour
# ---------------------------------------------------------------------------


WRITING_COMMANDS = (
    ("split", ["-o", "OUT"]),
    ("rotate", ["-o", "OUT.pdf"]),
    ("pages", ["-o", "OUT.pdf", "--select", "1"]),
    ("reorder", ["-o", "OUT.pdf", "--order", "1,2,3,4"]),
    ("sanitize", ["-o", "OUT.pdf"]),
)


@pytest.mark.parametrize(("command", "extra"), WRITING_COMMANDS)
def test_an_existing_output_needs_force(
    real_router: None, source: Path, tmp_path: Path, command: str, extra: list[str]
) -> None:
    """The pre-existing file is named after the command's own placeholder —
    ``OUT.pdf`` for a file-producing command, bare ``OUT`` for `split`, whose
    output is a directory (ADR 0031) — so it sits exactly where
    ``OutputTarget.resolve`` will actually check, file-shaped or not."""
    placeholder = next(a for a in extra if a.startswith("OUT"))
    out = tmp_path / placeholder.replace("OUT", "existing")
    out.write_bytes(b"a previous run")
    args = [command, str(source), *[str(out) if a.startswith("OUT") else a for a in extra]]

    result = runner.invoke(app, args)

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "output.exists" in text
    assert "Traceback" not in text
    assert out.read_bytes() == b"a previous run"


@pytest.mark.parametrize(("command", "extra"), WRITING_COMMANDS)
def test_a_dry_run_writes_nothing(
    real_router: None, source: Path, tmp_path: Path, command: str, extra: list[str]
) -> None:
    out = tmp_path / "out"
    args = [
        command,
        str(source),
        *[str(out) if a.startswith("OUT") else a for a in extra],
        "--dry-run",
    ]

    result = runner.invoke(app, args)

    assert result.exit_code == 0, shown(result)
    assert "Dry run" in shown(result)
    assert not out.exists()


@pytest.mark.parametrize(("command", "extra"), WRITING_COMMANDS)
def test_a_cloud_engine_is_refused(
    real_router: None, source: Path, tmp_path: Path, command: str, extra: list[str]
) -> None:
    """M2 is pypdf-only; the router refuses and the CLI merely reports."""
    out = tmp_path / "out"
    args = [
        command,
        str(source),
        *[str(out) if a.startswith("OUT") else a for a in extra],
        "--engine",
        "cloud",
    ]

    result = runner.invoke(app, args)

    assert result.exit_code == EXIT_FAILURE
    assert "engine.not_supported" in shown(result)
