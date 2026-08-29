"""The `compress` command and `doctor`'s external-binary reporting.

Two things are worth checking at this layer, and neither is compression.

**That the command reaches the router with what the user typed** — the CLI does
not know what Ghostscript is and must not learn.

**That `doctor` and `compress` consult one list.** They used to be two: the
mapping lived in `cli/main.py`, where the tools could not reach it. A test that
they agree is what keeps them from drifting apart again.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from docmax.cli.execution import EXIT_FAILURE
from docmax.cli.main import app
from docmax.core.config import Config
from docmax.core.router import EngineRouter
from docmax.tools import _binaries

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return _ANSI.sub("", text)


def shown(result: Any) -> str:
    return plain(result.stdout) + plain(result.stderr)


def write_pdf(path: Path, pages: int = 2) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


@pytest.fixture
def real_router(monkeypatch: pytest.MonkeyPatch) -> None:
    router = EngineRouter(config=Config(), consent=None)
    monkeypatch.setattr("docmax.cli.execution.build_router", lambda: router)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return write_pdf(tmp_path / "doc.pdf", 2)


def install_fake_gs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    """Point compress at a real stand-in program, through the real mechanism."""
    script = tmp_path / "fake_gs.py"
    script.write_text(
        "import sys\n"
        "args = sys.argv[1:]\n"
        "out = next(a.split('=', 1)[1] for a in args if a.startswith('-sOutputFile='))\n"
        "src = args[-1]\n" + body,
        encoding="utf-8",
    )
    real_run = _binaries.run

    def run_fake(command: Any, **kwargs: Any) -> Any:
        return real_run([sys.executable, str(script), *[str(c) for c in command[1:]]], **kwargs)

    monkeypatch.setattr(_binaries, "find", lambda name: sys.executable)
    monkeypatch.setattr(_binaries, "require", lambda name, *, tool: sys.executable)
    monkeypatch.setattr(_binaries, "run", run_fake)


COPIES = "import shutil; shutil.copyfile(src, out)\n"


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_compress_is_offered() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "compress" in plain(result.stdout)


def test_compress_writes_a_document(
    real_router: None, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_gs(monkeypatch, tmp_path, COPIES)
    out = tmp_path / "small.pdf"

    result = runner.invoke(app, ["compress", str(source), "-o", str(out)])

    assert result.exit_code == 0, shown(result)
    assert out.is_file()


def test_the_preset_reaches_the_tool(
    real_router: None, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_gs(
        monkeypatch,
        tmp_path,
        "assert '-dPDFSETTINGS=/screen' in args, args\nimport shutil; shutil.copyfile(src, out)\n",
    )

    result = runner.invoke(
        app,
        ["compress", str(source), "-o", str(tmp_path / "out.pdf"), "--preset", "screen"],
    )

    assert result.exit_code == 0, shown(result)


def test_compress_requires_an_output(real_router: None, source: Path) -> None:
    result = runner.invoke(app, ["compress", str(source)])

    assert result.exit_code == 2
    assert "--output" in shown(result)


def test_an_existing_output_needs_force(
    real_router: None, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_gs(monkeypatch, tmp_path, COPIES)
    out = tmp_path / "out.pdf"
    out.write_bytes(b"a previous run")

    result = runner.invoke(app, ["compress", str(source), "-o", str(out)])

    assert result.exit_code == EXIT_FAILURE
    assert "output.exists" in shown(result)
    assert out.read_bytes() == b"a previous run"


def test_an_unknown_preset_is_reported_without_a_traceback(
    real_router: None, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_gs(monkeypatch, tmp_path, COPIES)

    result = runner.invoke(
        app,
        ["compress", str(source), "-o", str(tmp_path / "out.pdf"), "--preset", "tiny"],
    )

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "input.invalid_parameter" in text
    assert "Traceback" not in text


def test_a_ghostscript_failure_is_reported_without_a_traceback(
    real_router: None, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_gs(
        monkeypatch, tmp_path, "import sys; sys.stderr.write('gs broke'); sys.exit(1)\n"
    )

    result = runner.invoke(app, ["compress", str(source), "-o", str(tmp_path / "out.pdf")])

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "dependency.tool_failed" in text
    assert "Traceback" not in text


def test_a_missing_ghostscript_tells_the_user_what_to_install(
    real_router: None, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The most likely first experience of this command, and it must be actionable."""
    monkeypatch.setattr(_binaries, "find", lambda name: None)

    result = runner.invoke(app, ["compress", str(source), "-o", str(tmp_path / "out.pdf")])

    text = shown(result)
    assert result.exit_code == EXIT_FAILURE
    assert "Ghostscript" in text
    assert "install" in text.lower(), "a command to type, not just a diagnosis"
    assert "Traceback" not in text


def test_a_dry_run_writes_nothing(
    real_router: None, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_gs(monkeypatch, tmp_path, COPIES)
    out = tmp_path / "out.pdf"

    result = runner.invoke(app, ["compress", str(source), "-o", str(out), "--dry-run"])

    assert result.exit_code == 0, shown(result)
    assert "Dry run" in shown(result)
    assert not out.exists()


def test_an_unconfigured_cloud_engine_is_refused(
    real_router: None, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--engine cloud` with no API key fails, and says which one is missing.

    Was `test_a_cloud_engine_is_refused`, which asserted compress had no cloud
    engine at all. It has one since M6, so the refusal moved rather than
    disappearing: `engine.not_supported` ("there is no such engine") became
    `engine.none_available` ("there is, and it is not set up"). The second is
    the more useful failure, and it must still be a failure — this is the test
    that would catch a run silently proceeding without credentials.
    """
    install_fake_gs(monkeypatch, tmp_path, COPIES)
    monkeypatch.delenv("DOCMAX_API_KEY", raising=False)

    result = runner.invoke(
        app,
        ["compress", str(source), "-o", str(tmp_path / "out.pdf"), "--engine", "cloud"],
    )

    assert result.exit_code == EXIT_FAILURE
    assert "engine.none_available" in shown(result)
    assert "API key" in shown(result)
    assert not (tmp_path / "out.pdf").exists()


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_doctor_still_reports_every_binary() -> None:
    """The M0 behaviour, preserved: one row per external program."""
    result = runner.invoke(app, ["doctor"])

    text = plain(result.stdout)
    assert result.exit_code == 0
    for name in ("gs", "tesseract", "pdftoppm", "pandoc"):
        assert name in text


def test_doctor_names_the_tools_that_need_each_binary() -> None:
    result = runner.invoke(app, ["doctor"])

    assert "compress" in plain(result.stdout), "gs is needed by compress"


def test_doctor_reports_a_missing_binary_with_its_install_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_binaries, "find", lambda name: None)

    result = runner.invoke(app, ["doctor"])

    text = shown(result)
    assert result.exit_code == 0, "doctor reports; it does not fail"
    assert "missing" in text
    assert "Install it with" in text, "a command to type"


def test_doctor_reports_an_available_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_binaries, "find", lambda name: "/usr/bin/" + name)

    result = runner.invoke(app, ["doctor"])

    text = shown(result)
    assert result.exit_code == 0
    assert "found" in text
    assert "All external tools available" in text


def test_doctor_never_mutates_anything(tmp_path: Path) -> None:
    """Reports only. `setup` is the one that installs, and it does not exist yet."""
    before = {p.name for p in tmp_path.iterdir()}

    runner.invoke(app, ["doctor"])

    assert {p.name for p in tmp_path.iterdir()} == before


def test_doctor_and_compress_read_the_same_declaration() -> None:
    """They used to be two lists in two layers. This is what keeps them one.

    `doctor` reports on `gs` and `compress` looks for `gs`; if either grew its
    own copy, a machine could be told it has Ghostscript by one and not the
    other.
    """
    from docmax.tools.compress.local import BINARY

    assert BINARY in {binary.name for binary in _binaries.EXTERNAL_BINARIES}
    assert "compress" in _binaries.describe(BINARY).used_by
