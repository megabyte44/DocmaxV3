"""`compress` — the first tool whose engine is another program.

Ghostscript is not installed on most development machines and is not a Python
dependency, so this is split deliberately:

* **Everything about the tool's behaviour** is tested with a fake Ghostscript —
  a real subprocess that writes a real PDF, so `atomic_path`, the validators and
  the error mapping are all genuinely exercised.
* **Real compression** is one test, marked `needs_binary`, skipped where
  Ghostscript is absent and required in CI.

The fake matters more than it sounds. What is being checked is not "does
Ghostscript work" — that is Artifex's problem — but "does DocMax behave when it
fails, hangs, lies about success, or is cancelled", and a real Ghostscript makes
those cases hard to produce on demand.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from docmax.core.cancellation import NEVER_CANCELLED, CancellationToken
from docmax.core.config import Config
from docmax.core.errors import (
    CancelledError,
    CorruptDocumentError,
    ExternalToolFailedError,
    InvalidParameterError,
    NoEngineAvailableError,
    OutputValidationError,
    UnsupportedFormatError,
)
from docmax.core.models import DocumentRef, Engine, OutputTarget
from docmax.core.protocols import NULL_PROGRESS
from docmax.core.registry import get_tool
from docmax.core.router import EngineRouter
from docmax.tools import _binaries
from docmax.tools.compress.validators import is_readable_pdf, page_count_is

if TYPE_CHECKING:
    from docmax.core.models import ToolResult

#: Skips locally, required in CI — the convention pyproject.toml documents.
needs_ghostscript = pytest.mark.needs_binary("gs")

GHOSTSCRIPT_PRESENT = _binaries.find("gs") is not None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def write_pdf(path: Path, pages: int = 3) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def page_count(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def staged(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.glob(".*"))


def fake_ghostscript(tmp_path: Path, body: str) -> Path:
    """A stand-in for `gs`: a real program, so the subprocess path is real.

    ``body`` runs with ``out`` bound to the ``-sOutputFile=`` value and ``src``
    to the input, which is enough to imitate every way Ghostscript can behave —
    including the ways it misbehaves.
    """
    script = tmp_path / "fake_gs.py"
    script.write_text(
        "import sys\n"
        "args = sys.argv[1:]\n"
        "out = next(a.split('=', 1)[1] for a in args if a.startswith('-sOutputFile='))\n"
        "src = args[-1]\n" + body,
        encoding="utf-8",
    )
    return script


def install_fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    """Point `compress` at the fake, through the real binary mechanism."""
    script = fake_ghostscript(tmp_path, body)
    real_run = _binaries.run

    def run_fake(command: Any, **kwargs: Any) -> Any:
        # Swap only the executable; every other argument is the one compress
        # actually built, so the flags are exercised as written.
        return real_run([sys.executable, str(script), *[str(c) for c in command[1:]]], **kwargs)

    monkeypatch.setattr(_binaries, "find", lambda name: sys.executable)
    monkeypatch.setattr(_binaries, "require", lambda name, *, tool: sys.executable)
    monkeypatch.setattr(_binaries, "run", run_fake)


COPIES_INPUT = "import shutil; shutil.copyfile(src, out)\n"


@pytest.fixture
def router() -> EngineRouter:
    return EngineRouter(config=Config())


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return write_pdf(tmp_path / "doc.pdf", 3)


def run(
    router: EngineRouter,
    source: Path,
    destination: Path,
    *,
    progress: Any = NULL_PROGRESS,
    cancellation: Any = NEVER_CANCELLED,
    **params: Any,
) -> ToolResult:
    return router.run(
        "compress",
        [DocumentRef.from_path(source)],
        OutputTarget(destination=destination, force=True),
        progress=progress,
        cancellation=cancellation,
        **params,
    )


# ---------------------------------------------------------------------------
# Registry and router
# ---------------------------------------------------------------------------


def test_compress_is_discoverable() -> None:
    spec = get_tool("compress")

    assert spec.name == "compress"
    assert spec.supports(Engine.LOCAL)
    assert spec.default_suffix == ".pdf"


def test_compress_has_both_engines() -> None:
    """Since M6. Installing Ghostscript is exactly the pain cloud exists to remove.

    Was `test_compress_has_no_cloud_engine_yet` until the engine existed. The
    condition M3 set for declaring it has not been relaxed, only met: the
    strategy loads and reports its own availability, which is asserted here
    rather than taken on trust.
    """
    spec = get_tool("compress")

    assert spec.supports(Engine.LOCAL)
    assert spec.supports(Engine.CLOUD)
    assert spec.load_strategy(Engine.CLOUD) is not None


def test_the_router_loads_the_local_strategy() -> None:
    from docmax.tools.compress.local import CompressLocal

    assert isinstance(get_tool("compress").load_strategy(Engine.LOCAL), CompressLocal)


# ---------------------------------------------------------------------------
# Ghostscript availability
# ---------------------------------------------------------------------------


def test_availability_follows_the_shared_binary_mechanism(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docmax.tools.compress.local import CompressLocal

    strategy = CompressLocal()
    monkeypatch.setattr(_binaries, "find", lambda name: None)
    assert strategy.is_available() is False

    monkeypatch.setattr(_binaries, "find", lambda name: "/usr/bin/gs")
    assert strategy.is_available() is True


def test_the_unavailable_reason_tells_the_user_what_to_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The router's own remedy can only be generic; it does not know what gs is."""
    from docmax.tools.compress.local import CompressLocal

    monkeypatch.setattr(_binaries, "find", lambda name: None)

    reason = CompressLocal().unavailable_reason()

    assert reason is not None
    assert "Ghostscript" in reason
    assert "install" in reason.lower(), "a command to type, not a category of problem"


def test_a_missing_ghostscript_is_reported_not_crashed(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_binaries, "find", lambda name: None)

    with pytest.raises(NoEngineAvailableError) as caught:
        run(router, source, tmp_path / "out.pdf")

    assert "Ghostscript" in str(caught.value)


# ---------------------------------------------------------------------------
# Behaviour, against a fake Ghostscript
# ---------------------------------------------------------------------------


def test_compress_produces_an_output(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, COPIES_INPUT)
    out = tmp_path / "small.pdf"

    result = run(router, source, out)

    assert out.is_file()
    assert page_count(out) == 3
    assert result.outputs == (out,)
    assert result.engine_used is Engine.LOCAL


def test_the_result_reports_the_saving(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, COPIES_INPUT)
    out = tmp_path / "small.pdf"

    result = run(router, source, out)

    details = result.details
    assert details["original_bytes"] == source.stat().st_size
    assert details["compressed_bytes"] == out.stat().st_size
    assert details["saved_bytes"] == details["original_bytes"] - details["compressed_bytes"]
    assert details["pages"] == 3
    assert details["preset"] == "ebook"


def test_a_negative_saving_is_reported_rather_than_hidden(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compression can make an already-optimised file larger. Say so."""
    install_fake(
        monkeypatch,
        tmp_path,
        "import shutil; shutil.copyfile(src, out)\n"
        "open(out, 'ab').write(b'%' + b'padding' * 500)\n",
    )
    out = tmp_path / "bigger.pdf"

    result = run(router, source, out)

    assert result.details["saved_bytes"] < 0


@pytest.mark.parametrize("preset", ["screen", "ebook", "printer", "prepress", "default"])
def test_every_ghostscript_preset_is_accepted(
    router: EngineRouter,
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preset: str,
) -> None:
    install_fake(monkeypatch, tmp_path, COPIES_INPUT)

    result = run(router, source, tmp_path / f"{preset}.pdf", preset=preset)

    assert result.details["preset"] == preset


def test_the_preset_reaches_ghostscript(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passed as Ghostscript's own `-dPDFSETTINGS=/name`, not a DocMax synonym."""
    install_fake(
        monkeypatch,
        tmp_path,
        "assert '-dPDFSETTINGS=/screen' in args, args\nimport shutil; shutil.copyfile(src, out)\n",
    )

    run(router, source, tmp_path / "out.pdf", preset="screen")


def test_batch_flags_are_passed(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without -dBATCH/-dNOPAUSE Ghostscript waits on stdin, which reads as a hang."""
    install_fake(
        monkeypatch,
        tmp_path,
        "for flag in ('-dBATCH', '-dNOPAUSE', '-dSAFER'):\n"
        "    assert flag in args, (flag, args)\n"
        "import shutil; shutil.copyfile(src, out)\n",
    )

    run(router, source, tmp_path / "out.pdf")


@pytest.mark.parametrize("preset", ["tiny", "", "SCREEN!", 5])
def test_an_unknown_preset_is_refused(
    router: EngineRouter,
    source: Path,
    tmp_path: Path,
    preset: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake(monkeypatch, tmp_path, COPIES_INPUT)

    with pytest.raises(InvalidParameterError) as caught:
        run(router, source, tmp_path / "out.pdf", preset=preset)

    assert "ebook" in (caught.value.remedy or ""), "the real presets are listed"


# ---------------------------------------------------------------------------
# When Ghostscript misbehaves
# ---------------------------------------------------------------------------


def test_a_ghostscript_failure_becomes_a_typed_error(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(
        monkeypatch,
        tmp_path,
        "import sys; sys.stderr.write('**** Unable to open the initial device'); sys.exit(1)\n",
    )

    with pytest.raises(ExternalToolFailedError) as caught:
        run(router, source, tmp_path / "out.pdf")

    assert "Unable to open the initial device" in str(caught.value)


def test_ghostscript_exiting_zero_with_no_output_is_caught(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It genuinely does this. Delivering nothing as success would be worse."""
    install_fake(monkeypatch, tmp_path, "pass\n")

    with pytest.raises(OutputValidationError):
        run(router, source, tmp_path / "out.pdf")


def test_an_empty_output_is_caught(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, "open(out, 'wb').close()\n")

    with pytest.raises(OutputValidationError):
        run(router, source, tmp_path / "out.pdf")


def test_losing_pages_is_caught(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure a user would be least likely to notice: smaller, and wrong."""
    install_fake(
        monkeypatch,
        tmp_path,
        "from pypdf import PdfReader, PdfWriter\n"
        "w = PdfWriter(); w.add_page(PdfReader(src).pages[0])\n"
        "open(out, 'wb').close()\n"
        "with open(out, 'wb') as fh: w.write(fh)\n",
    )

    with pytest.raises(OutputValidationError) as caught:
        run(router, source, tmp_path / "out.pdf")

    assert caught.value.context["expected"] == 3
    assert caught.value.context["actual"] == 1


def test_garbage_output_is_caught(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, "open(out, 'wb').write(b'not a pdf')\n")

    with pytest.raises(OutputValidationError):
        run(router, source, tmp_path / "out.pdf")


# ---------------------------------------------------------------------------
# Atomic output — the first consumer of atomic_path
# ---------------------------------------------------------------------------


def test_a_failure_leaves_the_previous_output_untouched(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, "import sys; sys.exit(1)\n")
    out = tmp_path / "out.pdf"
    out.write_bytes(b"a previous run's output")

    with pytest.raises(ExternalToolFailedError):
        run(router, source, out)

    assert out.read_bytes() == b"a previous run's output"
    assert staged(tmp_path) == []


def test_a_failure_creates_no_output_at_all(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, "import sys; sys.exit(1)\n")
    out = tmp_path / "out.pdf"

    with pytest.raises(ExternalToolFailedError):
        run(router, source, out)

    assert not out.exists()
    assert staged(tmp_path) == []


def test_a_rejected_output_leaves_nothing_behind(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validators run against the staged file, so a bad result is never delivered."""
    install_fake(monkeypatch, tmp_path, "open(out, 'wb').write(b'not a pdf')\n")
    out = tmp_path / "out.pdf"
    out.write_bytes(b"the original")

    with pytest.raises(OutputValidationError):
        run(router, source, out)

    assert out.read_bytes() == b"the original"
    assert staged(tmp_path) == []


def test_no_staged_file_survives_success(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, COPIES_INPUT)

    run(router, source, tmp_path / "out.pdf")

    assert staged(tmp_path) == []


# ---------------------------------------------------------------------------
# Input validation, progress and cancellation
# ---------------------------------------------------------------------------


def test_a_non_pdf_is_refused_before_ghostscript_is_invoked(
    router: EngineRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The input is checked before the binary runs, so nothing is launched in vain."""
    install_fake(
        monkeypatch,
        tmp_path,
        "raise AssertionError('ghostscript should not have run')\n",
    )
    notes = tmp_path / "notes.txt"
    notes.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(UnsupportedFormatError):
        run(router, notes, tmp_path / "out.pdf")


def test_a_missing_engine_outranks_a_bad_argument(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With Ghostscript absent, every compress failure is "Ghostscript is absent".

    The router resolves an engine before the tool sees anything, so a typo'd
    `--preset` on a machine without Ghostscript reports the missing binary. That
    is the right order — there is no point correcting an argument for an
    operation that cannot run either way — but it is worth pinning, because it
    means tool-level validation is only reachable once the engine is available.
    """
    monkeypatch.setattr(_binaries, "find", lambda name: None)

    with pytest.raises(NoEngineAvailableError):
        run(router, source, tmp_path / "out.pdf", preset="nonsense")


def test_a_corrupt_input_is_refused(
    router: EngineRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, COPIES_INPUT)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\ngarbage")

    with pytest.raises(CorruptDocumentError):
        run(router, broken, tmp_path / "out.pdf")


def test_progress_is_reported(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Indeterminate: Ghostscript reports nothing usable, and a made-up percentage
    would be a number the user cannot check."""
    install_fake(monkeypatch, tmp_path, COPIES_INPUT)
    events: list[tuple[str, Any]] = []

    class Recording:
        def start(self, description: str, *, total: int | None = None) -> None:
            events.append(("start", total))

        def advance(self, amount: int = 1) -> None:
            events.append(("advance", amount))

        def finish(self) -> None:
            events.append(("finish", None))

    run(router, source, tmp_path / "out.pdf", progress=Recording())

    assert ("start", None) in events, "no fabricated total"
    assert any(name == "advance" for name, _ in events)


def test_an_already_cancelled_run_produces_nothing(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, COPIES_INPUT)
    out = tmp_path / "out.pdf"
    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError):
        run(router, source, out, cancellation=token)

    assert not out.exists()
    assert staged(tmp_path) == []


def test_cancelling_mid_run_leaves_the_destination_untouched(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process is killed, and the staged file goes with it."""
    import threading

    install_fake(monkeypatch, tmp_path, "import time; time.sleep(30)\n")
    out = tmp_path / "out.pdf"
    out.write_bytes(b"a previous run")
    token = CancellationToken()
    threading.Timer(0.4, token.cancel).start()

    with pytest.raises(CancelledError):
        run(router, source, out, cancellation=token)

    assert out.read_bytes() == b"a previous run"
    assert staged(tmp_path) == []


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
    produced = write_pdf(tmp_path / "ok.pdf", 2)

    with pytest.raises(OutputValidationError) as caught:
        page_count_is(5)(produced)

    assert caught.value.context["expected"] == 5
    assert caught.value.context["actual"] == 2


# ---------------------------------------------------------------------------
# The real thing
# ---------------------------------------------------------------------------


@needs_ghostscript
@pytest.mark.skipif(not GHOSTSCRIPT_PRESENT, reason="Ghostscript is not installed")
def test_real_ghostscript_compresses_a_document(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """The one test that needs the real binary. Skipped locally, required in CI."""
    out = tmp_path / "small.pdf"

    result = run(router, source, out)

    assert out.is_file()
    assert page_count(out) == 3
    assert result.engine_version is not None
    assert result.engine_version.startswith("gs/")
    assert staged(tmp_path) == []


@needs_ghostscript
@pytest.mark.skipif(not GHOSTSCRIPT_PRESENT, reason="Ghostscript is not installed")
def test_real_ghostscript_is_found_by_the_shared_mechanism() -> None:
    found = _binaries.find("gs")

    assert found is not None
    assert Path(found).exists() or shutil.which(found)
