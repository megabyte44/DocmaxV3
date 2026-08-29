"""The M4 commands, end to end through the real registry, router and tools.

Integration tests, for the reason `test_cli_m2.py` gives: `test_cli_merge.py`
already pins the *negative* -- that the CLI decides nothing -- with fakes and a
structural check, and repeating that five times would test one thing five times.
What is worth checking here is that each command's arguments arrive intact at a
real tool and that a real file comes out the other side.

Two things get more attention than the M2 equivalents, because they are new at
this layer:

* **A password must never reach the terminal.** These commands take one, and the
  renderers print `result.details`. Every path that could leak it is asserted
  against explicitly.
* **`stamp` passes two inputs through one `--stamp` flag.** That is what makes
  the in-place check cover the overlay, so the refusal is tested from the CLI
  rather than only at the router.

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

COMMANDS = ("watermark", "stamp", "protect", "unlock", "permissions")

#: RC4 so these tests do not depend on the optional `crypto` extra. The default
#: algorithm is covered in `test_m4_tools.py`, which skips when it is absent.
RC4 = "RC4-128"


def plain(text: str) -> str:
    return _ANSI.sub("", text)


def shown(result: Any) -> str:
    """Both streams: results go to stdout, diagnostics to stderr."""
    return plain(result.stdout) + plain(result.stderr)


def write_pdf(path: Path, pages: int = 3, width: float = 300, height: float = 400) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=height)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def page_count(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def is_encrypted(path: Path) -> bool:
    from pypdf import PdfReader

    return bool(PdfReader(str(path)).is_encrypted)


def text_on(path: Path, index: int = 0) -> str:
    from pypdf import PdfReader

    return PdfReader(str(path)).pages[index].extract_text()


@pytest.fixture
def real_router(monkeypatch: pytest.MonkeyPatch) -> None:
    """The genuine registry, router and tools; only the config is kept off disk."""
    router = EngineRouter(config=Config(), consent=None)
    monkeypatch.setattr("docmax.cli.execution.build_router", lambda: router)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return write_pdf(tmp_path / "doc.pdf", 3)


@pytest.fixture
def overlay(tmp_path: Path) -> Path:
    return write_pdf(tmp_path / "logo.pdf", 1, width=80, height=40)


@pytest.fixture
def sealed(real_router: None, source: Path, tmp_path: Path) -> Path:
    """A locked document, produced by the command under test rather than by hand."""
    out = tmp_path / "sealed.pdf"
    result = runner.invoke(
        app,
        ["protect", str(source), "-o", str(out), "--password", "pw", "--algorithm", RC4],
    )
    assert result.exit_code == 0, shown(result)
    return out


# ---------------------------------------------------------------------------
# Surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", COMMANDS)
def test_every_m4_command_is_offered(command: str) -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert command in plain(result.stdout)


@pytest.mark.parametrize("command", COMMANDS)
def test_every_m4_command_has_help(command: str) -> None:
    result = runner.invoke(app, [command, "--help"])

    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# watermark
# ---------------------------------------------------------------------------


def test_watermark_writes_a_marked_pdf(real_router: None, source: Path, tmp_path: Path) -> None:
    out = tmp_path / "marked.pdf"

    result = runner.invoke(app, ["watermark", str(source), "-o", str(out), "--text", "DRAFT"])

    assert result.exit_code == 0, shown(result)
    assert "DRAFT" in text_on(out)
    assert page_count(out) == 3


def test_watermark_honours_a_page_selection(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "marked.pdf"

    result = runner.invoke(
        app, ["watermark", str(source), "-o", str(out), "--text", "X", "--pages", "1"]
    )

    assert result.exit_code == 0, shown(result)
    assert "X" in text_on(out, 0)
    assert "X" not in text_on(out, 2)


def test_watermark_honours_position_size_opacity_and_angle(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    """Every knob reaches the tool, rather than only the ones with defaults."""
    out = tmp_path / "marked.pdf"

    result = runner.invoke(
        app,
        [
            "watermark",
            str(source),
            "-o",
            str(out),
            "--text",
            "SAMPLE",
            "--position",
            "top-right",
            "--size",
            "18",
            "--opacity",
            "0.9",
            "--angle",
            "0",
        ],
    )

    assert result.exit_code == 0, shown(result)
    assert "SAMPLE" in text_on(out)


def test_watermark_reports_a_bad_position_without_a_traceback(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "watermark",
            str(source),
            "-o",
            str(tmp_path / "o.pdf"),
            "--text",
            "X",
            "--position",
            "middle-ish",
        ],
    )

    assert result.exit_code == EXIT_FAILURE
    assert "input.invalid_parameter" in shown(result)
    assert "Traceback" not in shown(result)


def test_watermark_needs_an_existing_output_to_be_forced(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "marked.pdf"
    out.write_bytes(b"already here")

    refused = runner.invoke(app, ["watermark", str(source), "-o", str(out), "--text", "X"])
    assert refused.exit_code == EXIT_FAILURE
    assert "output.exists" in shown(refused)
    assert out.read_bytes() == b"already here"

    forced = runner.invoke(
        app, ["watermark", str(source), "-o", str(out), "--text", "X", "--force"]
    )
    assert forced.exit_code == 0, shown(forced)
    assert page_count(out) == 3


# ---------------------------------------------------------------------------
# stamp
# ---------------------------------------------------------------------------


def test_stamp_writes_a_stamped_pdf(
    real_router: None, source: Path, overlay: Path, tmp_path: Path
) -> None:
    out = tmp_path / "stamped.pdf"

    result = runner.invoke(app, ["stamp", str(source), "-o", str(out), "--stamp", str(overlay)])

    assert result.exit_code == 0, shown(result)
    assert page_count(out) == 3, "the overlay's pages are drawn, not appended"


def test_stamp_honours_position_scale_and_pages(
    real_router: None, source: Path, overlay: Path, tmp_path: Path
) -> None:
    out = tmp_path / "stamped.pdf"

    result = runner.invoke(
        app,
        [
            "stamp",
            str(source),
            "-o",
            str(out),
            "--stamp",
            str(overlay),
            "--position",
            "top-left",
            "--scale",
            "0.5",
            "--pages",
            "2-3",
        ],
    )

    assert result.exit_code == 0, shown(result)
    assert page_count(out) == 3


def test_stamp_refuses_to_overwrite_the_overlay_it_is_reading(
    real_router: None, source: Path, overlay: Path
) -> None:
    """The whole reason `--stamp` is an input: the in-place check covers it."""
    result = runner.invoke(
        app, ["stamp", str(source), "-o", str(overlay), "--stamp", str(overlay), "--force"]
    )

    assert result.exit_code == EXIT_FAILURE
    assert "output.in_place_overwrite" in shown(result)
    assert page_count(overlay) == 1, "the overlay is intact"


def test_stamp_reports_a_missing_overlay_without_a_traceback(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "stamp",
            str(source),
            "-o",
            str(tmp_path / "o.pdf"),
            "--stamp",
            str(tmp_path / "gone.pdf"),
        ],
    )

    assert result.exit_code != 0
    assert "Traceback" not in shown(result)


# ---------------------------------------------------------------------------
# protect
# ---------------------------------------------------------------------------


def test_protect_writes_an_encrypted_pdf(real_router: None, sealed: Path) -> None:
    assert is_encrypted(sealed)


def test_protect_honours_an_allow_list(real_router: None, source: Path, tmp_path: Path) -> None:
    from pypdf import PdfReader

    from docmax.tools import _permissions

    out = tmp_path / "sealed.pdf"

    result = runner.invoke(
        app,
        [
            "protect",
            str(source),
            "-o",
            str(out),
            "--password",
            "pw",
            "--allow",
            "print",
            "--allow",
            "copy",
            "--algorithm",
            RC4,
        ],
    )

    assert result.exit_code == 0, shown(result)
    reader = PdfReader(str(out))
    reader.decrypt("pw")
    granted = _permissions.describe(reader.user_access_permissions)
    assert granted["print"] is True
    assert granted["copy"] is True
    assert granted["modify"] is False


def test_protect_honours_a_distinct_owner_password(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    from pypdf import PasswordType, PdfReader

    out = tmp_path / "sealed.pdf"

    result = runner.invoke(
        app,
        [
            "protect",
            str(source),
            "-o",
            str(out),
            "--password",
            "user-pw",
            "--owner-password",
            "owner-pw",
            "--algorithm",
            RC4,
        ],
    )

    assert result.exit_code == 0, shown(result)
    assert PdfReader(str(out)).decrypt("owner-pw") == PasswordType.OWNER_PASSWORD
    assert PdfReader(str(out)).decrypt("user-pw") == PasswordType.USER_PASSWORD


def test_protect_never_prints_the_password(real_router: None, source: Path, tmp_path: Path) -> None:
    """The renderers print `result.details`, so this is a real path, not a hypothetical."""
    out = tmp_path / "sealed.pdf"

    result = runner.invoke(
        app,
        [
            "protect",
            str(source),
            "-o",
            str(out),
            "--password",
            "hunter2",
            "--owner-password",
            "correct-horse",
            "--algorithm",
            RC4,
        ],
    )

    assert result.exit_code == 0, shown(result)
    assert "hunter2" not in shown(result)
    assert "correct-horse" not in shown(result)


def test_protect_reports_a_bad_algorithm_without_a_traceback(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "protect",
            str(source),
            "-o",
            str(tmp_path / "o.pdf"),
            "--password",
            "pw",
            "--algorithm",
            "ROT13",
        ],
    )

    assert result.exit_code == EXIT_FAILURE
    assert "input.invalid_parameter" in shown(result)
    assert "Traceback" not in shown(result)


def test_protect_refuses_an_already_encrypted_document(
    real_router: None, sealed: Path, tmp_path: Path
) -> None:
    """Two passwords on one file, one of them unknown, is not a useful outcome."""
    result = runner.invoke(
        app,
        [
            "protect",
            str(sealed),
            "-o",
            str(tmp_path / "twice.pdf"),
            "--password",
            "pw",
            "--algorithm",
            RC4,
        ],
    )

    assert result.exit_code == EXIT_FAILURE
    assert "input.encrypted" in shown(result)


# ---------------------------------------------------------------------------
# unlock
# ---------------------------------------------------------------------------


def test_unlock_writes_an_open_pdf(real_router: None, sealed: Path, tmp_path: Path) -> None:
    out = tmp_path / "open.pdf"

    result = runner.invoke(app, ["unlock", str(sealed), "-o", str(out), "--password", "pw"])

    assert result.exit_code == 0, shown(result)
    assert not is_encrypted(out)
    assert page_count(out) == 3


def test_unlock_reports_a_wrong_password_without_a_traceback(
    real_router: None, sealed: Path, tmp_path: Path
) -> None:
    out = tmp_path / "open.pdf"

    result = runner.invoke(app, ["unlock", str(sealed), "-o", str(out), "--password", "wrong"])

    assert result.exit_code == EXIT_FAILURE
    assert "input.encrypted" in shown(result)
    assert "Traceback" not in shown(result)
    assert not out.exists(), "nothing is written when the password does not open it"


def test_unlock_never_prints_the_password(real_router: None, sealed: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["unlock", str(sealed), "-o", str(tmp_path / "open.pdf"), "--password", "pw"]
    )

    assert result.exit_code == 0, shown(result)
    assert "pw" not in shown(result).replace(str(tmp_path), "")


# ---------------------------------------------------------------------------
# permissions
# ---------------------------------------------------------------------------


def test_permissions_prints_a_table_and_writes_nothing(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    before = sorted(p.name for p in tmp_path.iterdir())

    result = runner.invoke(app, ["permissions", str(source)])

    assert result.exit_code == 0, shown(result)
    assert "print" in shown(result)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_permissions_takes_no_output_option(real_router: None, source: Path) -> None:
    """Read-only, and the CLI says so by not offering the option at all.

    Matched on `--output` rather than on `-o`, which appears in the help *prose*
    explaining why there is none.
    """
    assert "--output" not in plain(runner.invoke(app, ["permissions", "--help"]).stdout)


def test_permissions_says_an_unencrypted_document_restricts_nothing(
    real_router: None, source: Path
) -> None:
    result = runner.invoke(app, ["permissions", str(source)])

    assert result.exit_code == 0, shown(result)
    assert "Not encrypted" in shown(result)


def test_permissions_reports_what_protect_granted(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "sealed.pdf"
    runner.invoke(
        app,
        [
            "protect",
            str(source),
            "-o",
            str(out),
            "--password",
            "pw",
            "--allow",
            "print",
            "--algorithm",
            RC4,
        ],
    )

    result = runner.invoke(app, ["permissions", str(out), "--password", "pw"])

    assert result.exit_code == 0, shown(result)
    assert "advisory" in shown(result)


def test_permissions_asks_for_a_password_without_a_traceback(
    real_router: None, sealed: Path
) -> None:
    result = runner.invoke(app, ["permissions", str(sealed)])

    assert result.exit_code == EXIT_FAILURE
    assert "input.encrypted" in shown(result)
    assert "Traceback" not in shown(result)


def test_permissions_never_prints_the_password(
    real_router: None, source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "sealed.pdf"
    runner.invoke(
        app,
        ["protect", str(source), "-o", str(out), "--password", "swordfish", "--algorithm", RC4],
    )

    result = runner.invoke(app, ["permissions", str(out), "--password", "swordfish"])

    assert result.exit_code == 0, shown(result)
    assert "swordfish" not in shown(result)


# ---------------------------------------------------------------------------
# Dry runs -- the router answers without touching a strategy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("watermark", ["--text", "X"]),
        ("protect", ["--password", "pw"]),
        ("unlock", ["--password", "pw"]),
    ],
)
def test_a_dry_run_writes_nothing(
    real_router: None, source: Path, tmp_path: Path, command: str, extra: list[str]
) -> None:
    out = tmp_path / "never.pdf"

    result = runner.invoke(app, [command, str(source), "-o", str(out), "--dry-run", *extra])

    assert result.exit_code == 0, shown(result)
    assert "Dry run" in shown(result)
    assert not out.exists()


def test_a_stamp_dry_run_writes_nothing(
    real_router: None, source: Path, overlay: Path, tmp_path: Path
) -> None:
    out = tmp_path / "never.pdf"

    result = runner.invoke(
        app, ["stamp", str(source), "-o", str(out), "--stamp", str(overlay), "--dry-run"]
    )

    assert result.exit_code == 0, shown(result)
    assert not out.exists()
