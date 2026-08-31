"""The three M5 tools and the shared format vocabulary.

Two of the three shell out, so this file follows `test_compress.py`'s division
rather than `test_m4_tools.py`'s:

* **Everything about behaviour** is tested against a fake Pandoc and a fake
  pdftoppm — real programs, run as real subprocesses through the real
  `_binaries` mechanism. What is being checked is not "does Pandoc work", which
  is not our problem, but "does DocMax behave when it fails, hangs, lies about
  success, or is cancelled" — and a real binary makes those cases hard to
  produce on demand.
* **Real conversions** are marked `needs_binary`, skipped where the program is
  absent and required in CI.

`from-images` needs no fake: img2pdf and Pillow are Python packages, so the real
thing runs here.

The format vocabulary gets its own section at the end. ADR 0010 says there is
one declaration and that `docmax formats` renders it; the tests that hold that
true are the ones asserting a tool's accepted set *is* the declaration rather
than merely agreeing with it today.
"""

from __future__ import annotations

import importlib.util
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
    ExternalToolTimeoutError,
    InvalidParameterError,
    NoEngineAvailableError,
    OutputValidationError,
    UnsupportedFormatError,
)
from docmax.core.models import DocumentRef, Engine, OutputTarget
from docmax.core.protocols import NULL_PROGRESS
from docmax.core.registry import get_tool
from docmax.core.router import EngineRouter
from docmax.tools import _binaries, _formats

if TYPE_CHECKING:
    from docmax.core.models import ToolResult

M5 = ("convert", "to-images", "from-images")

needs_pandoc = pytest.mark.needs_binary("pandoc")
needs_poppler = pytest.mark.needs_binary("pdftoppm")

#: A marker rather than a module-level `importorskip`, matching the `crypto`
#: extra's pattern in test_m4_tools.py — `from-images` is pure Python (no
#: binary), but Pillow and img2pdf are still the optional `images` extra, not
#: base dependencies. Only the tests that build or read a real image need this.
needs_images = pytest.mark.skipif(
    importlib.util.find_spec("PIL") is None or importlib.util.find_spec("img2pdf") is None,
    reason="the images extra is not installed",
)


# ---------------------------------------------------------------------------
# Fixtures and fakes
# ---------------------------------------------------------------------------


def write_pdf(path: Path, pages: int = 3) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def write_markdown(path: Path, text: str = "# Title\n\nSome words.\n") -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def write_image(path: Path, size: tuple[int, int] = (60, 40), colour: str = "red") -> Path:
    from PIL import Image

    Image.new("RGB", size, colour).save(path)
    return path


def page_count(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def staged(directory: Path) -> list[str]:
    """Anything the atomic helpers left behind. Should always be empty."""
    return sorted(p.name for p in directory.glob(".docmax-*"))


#: Every fake answers a version probe before reaching its body, and writes
#: nothing while doing so.
#:
#: This is not decoration. Both binary-backed tools ask their binary for a
#: version *after* the work is done, so a fake whose body writes
#: ``args[-1] + ".png"`` unconditionally is handed ``["-v"]`` and drops a file
#: named ``-v.png`` into the working directory — outside ``tmp_path``, and in a
#: checkout that means into the repository itself. It happened, and the two
#: files had to be deleted by hand.
_VERSION_PROBE = (
    "import sys\n"
    "args = sys.argv[1:]\n"
    "if args and args[0] in {'-v', '--version'}:\n"
    "    print('fake 1.0')\n"
    "    sys.exit(0)\n"
)


def install_fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    """Point the binary-backed tools at a fake, through the real mechanism.

    ``body`` runs with ``args`` bound to everything after the executable, so a
    fake sees exactly the flags the tool built. Only the executable is swapped;
    nothing else about the subprocess path is simulated.

    A version probe never reaches ``body`` — see :data:`_VERSION_PROBE`.
    """
    script = tmp_path / "fake_binary.py"
    script.write_text(_VERSION_PROBE + body, encoding="utf-8")
    real_run = _binaries.run

    def run_fake(command: Any, **kwargs: Any) -> Any:
        return real_run([sys.executable, str(script), *[str(c) for c in command[1:]]], **kwargs)

    monkeypatch.setattr(_binaries, "find", lambda name: sys.executable)
    monkeypatch.setattr(_binaries, "require", lambda name, *, tool: sys.executable)
    monkeypatch.setattr(_binaries, "run", run_fake)


#: A fake Pandoc that writes a plausible file for whatever `--to` asked for.
#: Zip-container formats get a real (empty) zip; everything else gets text.
PANDOC_WRITES_OUTPUT = """
import zipfile
out = args[args.index("--output") + 1]
writer = args[args.index("--to") + 1]
if writer in {"docx", "odt", "epub"}:
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("content.xml", "<x/>")
else:
    open(out, "w", encoding="utf-8").write("converted\\n")
"""

#: A fake pdftoppm that writes a real image for the format flag it was given.
#: `-singlefile` means the output is exactly `<root><ext>` with no page number.
PDFTOPPM_WRITES_IMAGES = """
SIGNATURES = {
    "-png": (b"\\x89PNG\\r\\n\\x1a\\n", ".png"),
    "-jpeg": (b"\\xff\\xd8\\xff", ".jpg"),
    "-tiff": (b"II*\\x00", ".tif"),
}
flag = next(a for a in args if a in SIGNATURES)
signature, extension = SIGNATURES[flag]
root = args[-1]
open(root + extension, "wb").write(signature + b"body")
"""


@pytest.fixture
def router() -> EngineRouter:
    """The real registry and router; only the config is kept off the real disk."""
    return EngineRouter(config=Config())


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return write_pdf(tmp_path / "doc.pdf", 3)


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    return write_markdown(tmp_path / "notes.md")


@pytest.fixture
def images(tmp_path: Path) -> list[Path]:
    return [
        write_image(tmp_path / "one.png", (60, 40), "red"),
        write_image(tmp_path / "two.jpg", (80, 30), "blue"),
        write_image(tmp_path / "three.png", (50, 50), "green"),
    ]


def run(
    router: EngineRouter,
    tool: str,
    inputs: Path | list[Path],
    destination: Path,
    *,
    progress: Any = NULL_PROGRESS,
    cancellation: Any = NEVER_CANCELLED,
    **params: Any,
) -> ToolResult:
    paths = [inputs] if isinstance(inputs, Path) else inputs
    return router.run(
        tool,
        [DocumentRef.from_path(path) for path in paths],
        OutputTarget(destination=destination, force=True),
        progress=progress,
        cancellation=cancellation,
        **params,
    )


class RecordingProgress:
    def __init__(self) -> None:
        self.events: list[str] = []

    def start(self, description: str, *, total: int | None = None) -> None:
        self.events.append("start")

    def advance(self, amount: int = 1) -> None:
        self.events.append("advance")

    def finish(self) -> None:
        self.events.append("finish")


# ---------------------------------------------------------------------------
# Registry and router — every tool, uniformly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", M5)
def test_every_m5_tool_is_discoverable(name: str) -> None:
    spec = get_tool(name)

    assert spec.name == name
    assert spec.supports(Engine.LOCAL)


def test_from_images_has_no_cloud_engine() -> None:
    """`from-images` is pure Python (img2pdf + Pillow), so uploading a document
    to run it would buy nothing — unlike `to-images`, which joined the cloud
    engines at ADR 0034 for the Poppler dependency it shares with `ocr`.
    """
    assert not get_tool("from-images").supports(Engine.CLOUD)


def test_to_images_gained_a_cloud_engine() -> None:
    """ADR 0034: `to-images` shares `ocr`'s Poppler dependency, so cloud helps it
    the same way it helps `ocr` — installing Poppler is the pain cloud removes.
    """
    spec = get_tool("to-images")

    assert spec.supports(Engine.LOCAL)
    assert spec.supports(Engine.CLOUD)


def test_convert_gained_a_cloud_engine_at_m6() -> None:
    """Installing Pandoc is the pain cloud exists to remove.

    The format boundary is not widened by it: ADR 0011's rules live in the
    shared `_formats` table, which both engines validate against.
    """
    spec = get_tool("convert")

    assert spec.supports(Engine.LOCAL)
    assert spec.supports(Engine.CLOUD)
    assert "pdf" not in _formats.convertible_names(), "the cloud engine widens nothing"


@pytest.mark.parametrize("name", M5)
def test_every_m5_tool_loads_its_strategy(name: str) -> None:
    strategy = get_tool(name).load_strategy(Engine.LOCAL)

    assert hasattr(strategy, "run")
    assert strategy.is_available() in {True, False}


def test_from_images_is_the_second_multi_input_tool() -> None:
    """`core/protocols.py` has named it beside `merge` since M1."""
    assert get_tool("from-images").accepts_multiple_inputs is True
    assert get_tool("convert").accepts_multiple_inputs is False
    assert get_tool("to-images").accepts_multiple_inputs is False


# ---------------------------------------------------------------------------
# convert — availability
# ---------------------------------------------------------------------------


def test_convert_reports_missing_pandoc_with_the_install_line() -> None:
    """The router's own remedy is generic; it does not know what Pandoc is."""
    from docmax.tools.convert.local import ConvertLocal

    strategy = ConvertLocal()
    if strategy.is_available():
        pytest.skip("pandoc is installed on this machine")

    reason = strategy.unavailable_reason()
    assert reason is not None
    assert "pandoc" in reason
    assert "install" in reason.lower()


def test_a_missing_pandoc_is_reported_not_crashed(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_binaries, "find", lambda name: None)

    with pytest.raises(NoEngineAvailableError) as caught:
        run(router, "convert", notes, tmp_path / "out.docx", to="docx")

    assert "pandoc" in str(caught.value)


# ---------------------------------------------------------------------------
# convert — behaviour, against a fake Pandoc
# ---------------------------------------------------------------------------


def test_convert_produces_an_output(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PANDOC_WRITES_OUTPUT)
    out = tmp_path / "notes.html"

    result = run(router, "convert", notes, out, to="html")

    assert out.exists()
    assert result.details["from"] == "md"
    assert result.details["to"] == "html"
    assert result.outputs == (out,)


@pytest.mark.parametrize("target", _formats.convertible_names())
def test_convert_writes_every_declared_target_format(
    router: EngineRouter,
    notes: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    """Parametrised over the shared table, so a format added there is covered here."""
    install_fake(monkeypatch, tmp_path, PANDOC_WRITES_OUTPUT)
    out = tmp_path / f"out{_formats.document(target).suffix}"

    result = run(router, "convert", notes, out, to=target)

    assert result.details["to"] == target
    assert out.exists()


def test_convert_passes_pandocs_own_reader_and_writer_names(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not DocMax synonyms: someone reading the Pandoc manual should recognise these."""
    install_fake(
        monkeypatch,
        tmp_path,
        'open(args[args.index("--output") + 1], "w").write(" ".join(args))\n',
    )
    out = tmp_path / "out.rst"

    run(router, "convert", notes, out, to="rst")

    written = out.read_text(encoding="utf-8")
    assert "--from markdown" in written
    assert "--to rst" in written


def test_convert_asks_for_a_standalone_document_by_default(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without it Pandoc emits an HTML fragment with no <html> element."""
    install_fake(
        monkeypatch,
        tmp_path,
        'open(args[args.index("--output") + 1], "w").write(" ".join(args))\n',
    )
    out = tmp_path / "out.html"

    run(router, "convert", notes, out, to="html")

    assert "--standalone" in out.read_text(encoding="utf-8")


def test_convert_can_be_told_not_to(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(
        monkeypatch,
        tmp_path,
        'open(args[args.index("--output") + 1], "w").write(" ".join(args))\n',
    )
    out = tmp_path / "out.html"

    result = run(router, "convert", notes, out, to="html", standalone=False)

    assert "--standalone" not in out.read_text(encoding="utf-8")
    assert result.details["standalone"] is False


# ---------------------------------------------------------------------------
# convert — the format boundary (ADR 0011)
# ---------------------------------------------------------------------------


def test_convert_refuses_pdf_as_a_target_and_explains_why(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--to pdf` is the thing people try first. It gets a reason, not 'unknown format'."""
    install_fake(monkeypatch, tmp_path, PANDOC_WRITES_OUTPUT)

    with pytest.raises(InvalidParameterError) as caught:
        run(router, "convert", notes, tmp_path / "out.pdf", to="pdf")

    message = str(caught.value)
    assert "LaTeX" in message
    assert "to-images" in message, "the message names what to use instead"


def test_convert_refuses_a_pdf_input_and_explains_why(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pandoc has no PDF reader. Saying so beats reporting an unsupported extension."""
    install_fake(monkeypatch, tmp_path, PANDOC_WRITES_OUTPUT)

    with pytest.raises(UnsupportedFormatError) as caught:
        run(router, "convert", source, tmp_path / "out.docx", to="docx")

    message = str(caught.value)
    assert "cannot read" in message
    assert "to-images" in message


def test_convert_refuses_a_target_it_has_never_heard_of(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PANDOC_WRITES_OUTPUT)

    with pytest.raises(InvalidParameterError, match="not a format"):
        run(router, "convert", notes, tmp_path / "out.xyz", to="mediawiki")


def test_convert_refuses_txt_as_an_input(
    router: EngineRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pandoc's `plain` is a writer with no matching reader, so txt is output only."""
    install_fake(monkeypatch, tmp_path, PANDOC_WRITES_OUTPUT)
    plain = tmp_path / "notes.txt"
    plain.write_text("hello", encoding="utf-8")

    with pytest.raises(UnsupportedFormatError, match="cannot read"):
        run(router, "convert", plain, tmp_path / "out.docx", to="docx")


def test_convert_can_still_write_txt(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PANDOC_WRITES_OUTPUT)

    result = run(router, "convert", notes, tmp_path / "out.txt", to="txt")

    assert result.details["to"] == "txt"


def test_convert_refuses_an_input_extension_nobody_recognises(
    router: EngineRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PANDOC_WRITES_OUTPUT)
    odd = tmp_path / "data.xyz"
    odd.write_text("?", encoding="utf-8")

    with pytest.raises(UnsupportedFormatError, match="not a format"):
        run(router, "convert", odd, tmp_path / "out.docx", to="docx")


def test_convert_needs_a_target_format(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PANDOC_WRITES_OUTPUT)

    with pytest.raises(InvalidParameterError, match="target format"):
        run(router, "convert", notes, tmp_path / "out.docx")


@pytest.mark.parametrize("spelling", ["docx", "DOCX", ".docx", " docx "])
def test_convert_accepts_a_format_however_it_is_spelled(
    router: EngineRouter,
    notes: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spelling: str,
) -> None:
    install_fake(monkeypatch, tmp_path, PANDOC_WRITES_OUTPUT)

    result = run(router, "convert", notes, tmp_path / "out.docx", to=spelling)

    assert result.details["to"] == "docx"


# ---------------------------------------------------------------------------
# convert — failure, cancellation and the destination
# ---------------------------------------------------------------------------


def test_a_failing_pandoc_leaves_the_destination_untouched(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, 'sys.stderr.write("pandoc: boom\\n"); sys.exit(1)\n')
    out = tmp_path / "out.html"
    out.write_text("the original", encoding="utf-8")

    with pytest.raises(ExternalToolFailedError):
        run(router, "convert", notes, out, to="html")

    assert out.read_text(encoding="utf-8") == "the original"
    assert not staged(tmp_path)


def test_a_pandoc_that_writes_nothing_is_caught(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exiting zero having written nothing is a lie `atomic_path` already refuses."""
    install_fake(monkeypatch, tmp_path, "pass\n")
    out = tmp_path / "out.html"

    with pytest.raises(OutputValidationError):
        run(router, "convert", notes, out, to="html")

    assert not out.exists()


def test_a_docx_that_is_not_a_zip_is_caught_before_delivery(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A .docx is an Open Packaging container or it is not a Word document."""
    install_fake(
        monkeypatch,
        tmp_path,
        'open(args[args.index("--output") + 1], "w").write("not a zip")\n',
    )
    out = tmp_path / "out.docx"

    with pytest.raises(OutputValidationError, match="zip container"):
        run(router, "convert", notes, out, to="docx")

    assert not out.exists()


def test_whitespace_only_output_is_treated_as_a_failure(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(
        monkeypatch,
        tmp_path,
        'open(args[args.index("--output") + 1], "w").write("   \\n\\n")\n',
    )

    with pytest.raises(OutputValidationError, match="empty"):
        run(router, "convert", notes, tmp_path / "out.html", to="html")


def test_an_already_cancelled_convert_produces_nothing(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PANDOC_WRITES_OUTPUT)
    out = tmp_path / "out.html"
    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError):
        run(router, "convert", notes, out, to="html", cancellation=token)

    assert not out.exists()


def test_a_pandoc_that_hangs_is_killed_at_the_deadline(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v2 had no timeout anywhere; a hung xelatex hung the whole application."""
    install_fake(monkeypatch, tmp_path, "import time; time.sleep(30)\n")
    out = tmp_path / "out.html"

    with pytest.raises(ExternalToolTimeoutError):
        run(
            router,
            "convert",
            notes,
            out,
            to="html",
            cancellation=CancellationToken(timeout=0.5),
        )

    assert not out.exists()
    assert not staged(tmp_path)


def test_convert_reports_progress_and_finishes(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PANDOC_WRITES_OUTPUT)
    progress = RecordingProgress()

    run(router, "convert", notes, tmp_path / "out.html", to="html", progress=progress)

    assert progress.events[0] == "start"
    assert progress.events[-1] == "finish"


def test_convert_refuses_to_write_over_its_own_input(notes: Path) -> None:
    """The case ADR 0003 names: `convert x.md --to md` writing over x.md."""
    from docmax.core.errors import InPlaceOverwriteError

    router = EngineRouter(config=Config())
    docs = [DocumentRef.from_path(notes)]

    with pytest.raises(InPlaceOverwriteError):
        router.target_for("convert", docs, requested=str(notes), force=True)


@needs_pandoc
def test_convert_really_converts_markdown_to_html(
    router: EngineRouter, notes: Path, tmp_path: Path
) -> None:
    out = tmp_path / "notes.html"

    run(router, "convert", notes, out, to="html")

    produced = out.read_text(encoding="utf-8")
    assert "Title" in produced
    assert "<html" in produced.lower()


# ---------------------------------------------------------------------------
# to-images — availability
# ---------------------------------------------------------------------------


def test_to_images_needs_no_python_imaging_library() -> None:
    """pdftoppm writes the file itself; a second encoder would only re-encode it."""
    from docmax.tools.to_images import local

    assert "PIL" not in local.__dict__
    assert local.BINARY == "pdftoppm"


def test_to_images_names_poppler_not_just_the_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nobody installs a package called pdftoppm."""
    from docmax.tools.to_images.local import ToImagesLocal

    monkeypatch.setattr(_binaries, "find", lambda name: None)
    reason = ToImagesLocal().unavailable_reason()

    assert reason is not None
    assert "Poppler" in reason
    assert "install" in reason.lower()


def test_a_missing_poppler_is_reported_not_crashed(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_binaries, "find", lambda name: None)

    with pytest.raises(NoEngineAvailableError) as caught:
        run(router, "to-images", source, tmp_path / "pages")

    assert "pdftoppm" in str(caught.value)


# ---------------------------------------------------------------------------
# to-images — behaviour, against a fake pdftoppm
# ---------------------------------------------------------------------------


def test_to_images_writes_one_image_per_page(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PDFTOPPM_WRITES_IMAGES)
    out = tmp_path / "pages"

    result = run(router, "to-images", source, out)

    assert sorted(p.name for p in out.glob("*.png")) == [
        "doc-0001.png",
        "doc-0002.png",
        "doc-0003.png",
    ]
    assert result.details["files"] == 3


def test_to_images_names_pages_in_reading_order(
    router: EngineRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero-padded, so a directory listing sorts the way the document reads."""
    install_fake(monkeypatch, tmp_path, PDFTOPPM_WRITES_IMAGES)
    source = write_pdf(tmp_path / "doc.pdf", 12)
    out = tmp_path / "pages"

    run(router, "to-images", source, out)

    names = sorted(p.name for p in out.glob("*.png"))
    assert names[0] == "doc-0001.png"
    assert names[-1] == "doc-0012.png"


def test_to_images_honours_a_page_selection(
    router: EngineRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-contiguous selection is why this renders one page per invocation."""
    install_fake(monkeypatch, tmp_path, PDFTOPPM_WRITES_IMAGES)
    source = write_pdf(tmp_path / "doc.pdf", 8)
    out = tmp_path / "pages"

    result = run(router, "to-images", source, out, pages="2-3,7")

    assert sorted(p.name for p in out.glob("*.png")) == [
        "doc-0002.png",
        "doc-0003.png",
        "doc-0007.png",
    ]
    assert result.details["selection"] == "2-3,7"


@pytest.mark.parametrize("image_format", _formats.rasterisable_names())
def test_to_images_writes_every_declared_image_format(
    router: EngineRouter,
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    image_format: str,
) -> None:
    install_fake(monkeypatch, tmp_path, PDFTOPPM_WRITES_IMAGES)
    out = tmp_path / "pages"

    result = run(router, "to-images", source, out, format=image_format)

    assert result.details["format"] == image_format
    assert len(list(out.glob(f"*{_formats.image(image_format).suffix}"))) == 3


def test_the_format_flag_reaches_pdftoppm(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Poppler's own flag, not a DocMax synonym."""
    install_fake(
        monkeypatch,
        tmp_path,
        'open(args[-1] + ".jpg", "wb").write(b"\\xff\\xd8\\xff" + " ".join(args).encode())\n',
    )
    out = tmp_path / "pages"

    run(router, "to-images", source, out, format="jpeg")

    assert "-jpeg" in next(out.glob("*.jpg")).read_bytes().decode("utf-8", "replace")


def test_the_dpi_reaches_pdftoppm(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(
        monkeypatch,
        tmp_path,
        'open(args[-1] + ".png", "wb").write(b"\\x89PNG\\r\\n\\x1a\\n" + " ".join(args).encode())\n',
    )
    out = tmp_path / "pages"

    run(router, "to-images", source, out, dpi=300)

    assert "-r 300" in next(out.glob("*.png")).read_bytes().decode("utf-8", "replace")


def test_to_images_renders_one_page_per_invocation(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`-singlefile` with a one-page range is what makes DocMax own the filenames."""
    install_fake(
        monkeypatch,
        tmp_path,
        'open(args[-1] + ".png", "wb").write(b"\\x89PNG\\r\\n\\x1a\\n" + " ".join(args).encode())\n',
    )
    out = tmp_path / "pages"

    run(router, "to-images", source, out, pages="2")

    flags = next(out.glob("*.png")).read_bytes().decode("utf-8", "replace")
    assert "-singlefile" in flags
    assert "-f 2" in flags
    assert "-l 2" in flags


@pytest.mark.parametrize("image_format", ["bmp", "gif"])
def test_to_images_refuses_a_format_pdftoppm_cannot_write(
    router: EngineRouter,
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    image_format: str,
) -> None:
    """Declared for `from-images`, and not something Poppler rasterises to."""
    install_fake(monkeypatch, tmp_path, PDFTOPPM_WRITES_IMAGES)

    with pytest.raises(InvalidParameterError, match="cannot write"):
        run(router, "to-images", source, tmp_path / "pages", format=image_format)


def test_to_images_refuses_an_unknown_format(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PDFTOPPM_WRITES_IMAGES)

    with pytest.raises(InvalidParameterError, match="not an image format"):
        run(router, "to-images", source, tmp_path / "pages", format="webp")


@pytest.mark.parametrize("dpi", [0, -50, 5, 5000, "high", 1.5])
def test_to_images_refuses_a_nonsense_dpi(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dpi: Any
) -> None:
    install_fake(monkeypatch, tmp_path, PDFTOPPM_WRITES_IMAGES)

    with pytest.raises(InvalidParameterError):
        run(router, "to-images", source, tmp_path / "pages", dpi=dpi)


def test_to_images_refuses_a_non_pdf(
    router: EngineRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PDFTOPPM_WRITES_IMAGES)
    notes = tmp_path / "notes.txt"
    notes.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(UnsupportedFormatError):
        run(router, "to-images", notes, tmp_path / "pages")


def test_to_images_refuses_a_corrupt_pdf(
    router: EngineRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PDFTOPPM_WRITES_IMAGES)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\ngarbage")

    with pytest.raises(CorruptDocumentError):
        run(router, "to-images", broken, tmp_path / "pages")


# ---------------------------------------------------------------------------
# to-images — the header check, and the directory guarantee
# ---------------------------------------------------------------------------


def test_a_file_with_the_wrong_header_is_never_delivered(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact v2 bug: a .png with no PNG header, which nothing can open."""
    install_fake(monkeypatch, tmp_path, 'open(args[-1] + ".png", "wb").write(b"not an image")\n')
    out = tmp_path / "pages"

    with pytest.raises(OutputValidationError, match="no PNG header"):
        run(router, "to-images", source, out)

    assert not out.exists()


def test_an_empty_image_is_never_delivered(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, 'open(args[-1] + ".png", "wb").write(b"")\n')
    out = tmp_path / "pages"

    with pytest.raises(OutputValidationError, match="empty file"):
        run(router, "to-images", source, out)

    assert not out.exists()


def test_a_short_render_is_never_delivered(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pdftoppm writing only some of the pages must not look like success."""
    install_fake(
        monkeypatch,
        tmp_path,
        "import os\n"
        "root = args[-1]\n"
        'if not root.endswith("0003"):\n'
        '    open(root + ".png", "wb").write(b"\\x89PNG\\r\\n\\x1a\\n")\n',
    )
    out = tmp_path / "pages"

    with pytest.raises(OutputValidationError, match="Expected 3 image"):
        run(router, "to-images", source, out)

    assert not out.exists()


def test_a_failing_pdftoppm_leaves_no_partial_directory(
    router: EngineRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `atomic_dir` guarantee, exercised by the second tool to need it."""
    install_fake(
        monkeypatch,
        tmp_path,
        "root = args[-1]\n"
        'if root.endswith("0004"):\n'
        '    sys.stderr.write("pdftoppm: boom\\n"); sys.exit(1)\n'
        'open(root + ".png", "wb").write(b"\\x89PNG\\r\\n\\x1a\\n")\n',
    )
    source = write_pdf(tmp_path / "doc.pdf", 6)
    out = tmp_path / "pages"

    with pytest.raises(ExternalToolFailedError):
        run(router, "to-images", source, out)

    assert not out.exists(), "no directory holding the first three pages"
    assert not staged(tmp_path)


def test_a_cancelled_render_leaves_no_partial_directory(
    router: EngineRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PDFTOPPM_WRITES_IMAGES)
    source = write_pdf(tmp_path / "doc.pdf", 6)
    out = tmp_path / "pages"
    token = CancellationToken()

    class CancelAfterFirstPage:
        def start(self, description: str, *, total: int | None = None) -> None: ...

        def advance(self, amount: int = 1) -> None:
            token.cancel()

        def finish(self) -> None: ...

    with pytest.raises(CancelledError):
        run(
            router,
            "to-images",
            source,
            out,
            progress=CancelAfterFirstPage(),
            cancellation=token,
        )

    assert not out.exists()
    assert not staged(tmp_path)


def test_to_images_reports_progress_per_page(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PDFTOPPM_WRITES_IMAGES)
    progress = RecordingProgress()

    run(router, "to-images", source, tmp_path / "pages", progress=progress)

    assert progress.events[0] == "start"
    assert progress.events.count("advance") == 3
    assert progress.events[-1] == "finish"


def test_to_images_reports_every_file_it_wrote(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PDFTOPPM_WRITES_IMAGES)
    out = tmp_path / "pages"

    result = run(router, "to-images", source, out)

    assert len(result.outputs) == 3
    assert all(path.parent == out for path in result.outputs)
    assert all(path.exists() for path in result.outputs)


@needs_poppler
@needs_images
def test_to_images_really_renders_a_page(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    from PIL import Image

    out = tmp_path / "pages"

    run(router, "to-images", source, out, pages="1")

    rendered = next(out.glob("*.png"))
    with Image.open(rendered) as handle:
        assert handle.format == "PNG"
        assert handle.width > 0


# ---------------------------------------------------------------------------
# from-images
# ---------------------------------------------------------------------------


@needs_images
def test_from_images_builds_one_page_per_image(
    router: EngineRouter, images: list[Path], tmp_path: Path
) -> None:
    out = tmp_path / "album.pdf"

    result = run(router, "from-images", images, out)

    assert page_count(out) == 3
    assert result.details["pages"] == 3
    assert result.details["formats"] == ["png", "jpeg", "png"]


@needs_images
def test_from_images_keeps_argument_order(router: EngineRouter, tmp_path: Path) -> None:
    """Page order is argument order — the order the user can see on their command line."""
    from pypdf import PdfReader

    wide = write_image(tmp_path / "wide.png", (120, 20))
    tall = write_image(tmp_path / "tall.png", (20, 120))
    out = tmp_path / "album.pdf"

    run(router, "from-images", [wide, tall], out)

    pages = PdfReader(str(out)).pages
    assert pages[0].mediabox.width > pages[0].mediabox.height, "wide image first"
    assert pages[1].mediabox.height > pages[1].mediabox.width, "tall image second"


@needs_images
def test_from_images_sizes_each_page_to_its_image(router: EngineRouter, tmp_path: Path) -> None:
    """Nothing is scaled, cropped or letterboxed onto a fixed page size."""
    from pypdf import PdfReader

    small = write_image(tmp_path / "small.png", (60, 40))
    large = write_image(tmp_path / "large.png", (600, 400))
    out = tmp_path / "album.pdf"

    run(router, "from-images", [small, large], out)

    pages = PdfReader(str(out)).pages
    assert pages[1].mediabox.width > pages[0].mediabox.width


@needs_images
def test_from_images_produces_a_readable_pdf(
    router: EngineRouter, images: list[Path], tmp_path: Path
) -> None:
    from pypdf import PdfReader

    out = tmp_path / "album.pdf"

    run(router, "from-images", images, out)

    reader = PdfReader(str(out))
    assert not reader.is_encrypted
    assert len(reader.pages) == 3


@needs_images
def test_from_images_accepts_a_single_image(router: EngineRouter, tmp_path: Path) -> None:
    out = tmp_path / "one.pdf"

    run(router, "from-images", [write_image(tmp_path / "a.png")], out)

    assert page_count(out) == 1


@needs_images
@pytest.mark.parametrize("name", _formats.readable_image_names())
def test_from_images_reads_every_declared_image_format(
    router: EngineRouter, tmp_path: Path, name: str
) -> None:
    """Parametrised over the shared table, so a format added there is covered here."""
    suffix = _formats.image(name).suffix
    out = tmp_path / "album.pdf"

    run(router, "from-images", [write_image(tmp_path / f"a{suffix}")], out)

    assert page_count(out) == 1


@needs_images
def test_from_images_refuses_a_file_that_is_not_an_image(
    router: EngineRouter, tmp_path: Path
) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("not an image", encoding="utf-8")

    with pytest.raises(UnsupportedFormatError):
        run(router, "from-images", [notes], tmp_path / "album.pdf")


@needs_images
def test_from_images_refuses_a_png_that_is_not_a_png(router: EngineRouter, tmp_path: Path) -> None:
    """The extension says one thing and the bytes say another. Pillow settles it."""
    liar = tmp_path / "liar.png"
    liar.write_text("definitely not a png", encoding="utf-8")

    with pytest.raises(CorruptDocumentError, match="not a readable"):
        run(router, "from-images", [liar], tmp_path / "album.pdf")


@needs_images
def test_one_bad_image_is_reported_before_any_page_is_built(
    router: EngineRouter, tmp_path: Path
) -> None:
    """A bad file at position three should not cost two pages of work first."""
    good = write_image(tmp_path / "good.png")
    liar = tmp_path / "liar.png"
    liar.write_text("nope", encoding="utf-8")
    out = tmp_path / "album.pdf"

    with pytest.raises(CorruptDocumentError):
        run(router, "from-images", [good, good, liar], out)

    assert not out.exists()
    assert not staged(tmp_path)


@needs_images
def test_from_images_refuses_to_write_over_one_of_its_inputs(
    images: list[Path],
) -> None:
    """`from-images *.png -o page1.png` would destroy the image it was built from."""
    from docmax.core.errors import InPlaceOverwriteError

    router = EngineRouter(config=Config())
    docs = [DocumentRef.from_path(path) for path in images]

    with pytest.raises(InPlaceOverwriteError):
        router.target_for("from-images", docs, requested=str(images[0]), force=True)


@needs_images
def test_an_already_cancelled_assembly_produces_nothing(
    router: EngineRouter, images: list[Path], tmp_path: Path
) -> None:
    out = tmp_path / "album.pdf"
    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError):
        run(router, "from-images", images, out, cancellation=token)

    assert not out.exists()


@needs_images
def test_a_cancelled_assembly_leaves_the_destination_untouched(
    router: EngineRouter, images: list[Path], tmp_path: Path
) -> None:
    out = tmp_path / "album.pdf"
    out.write_bytes(b"the original")
    token = CancellationToken()

    class CancelAfterFirstImage:
        def start(self, description: str, *, total: int | None = None) -> None: ...

        def advance(self, amount: int = 1) -> None:
            token.cancel()

        def finish(self) -> None: ...

    with pytest.raises(CancelledError):
        run(
            router,
            "from-images",
            images,
            out,
            progress=CancelAfterFirstImage(),
            cancellation=token,
        )

    assert out.read_bytes() == b"the original"
    assert not staged(tmp_path)


@needs_images
def test_from_images_reports_progress_per_image(
    router: EngineRouter, images: list[Path], tmp_path: Path
) -> None:
    progress = RecordingProgress()

    run(router, "from-images", images, tmp_path / "album.pdf", progress=progress)

    assert progress.events[0] == "start"
    assert progress.events.count("advance") == 3
    assert progress.events[-1] == "finish"


def test_from_images_names_both_of_its_dependencies_when_they_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user with Pillow but not img2pdf needs to be told which one to install."""
    import importlib.util

    from docmax.tools.from_images import local

    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: None if name == "img2pdf" else object()
    )
    reason = local.FromImagesLocal().unavailable_reason()

    assert reason is not None
    assert "img2pdf" in reason
    assert "PIL" not in reason, "only the missing one is named"


def test_a_short_assembly_is_never_delivered(tmp_path: Path) -> None:
    """One page per image. A document with fewer has silently dropped one."""
    from docmax.tools.from_images.validators import page_count_is

    out = write_pdf(tmp_path / "two.pdf", 2)

    with pytest.raises(OutputValidationError, match="Expected 5 page"):
        page_count_is(5)(out)


# ---------------------------------------------------------------------------
# The shared format vocabulary — ADR 0010
# ---------------------------------------------------------------------------


def test_convert_accepts_exactly_what_the_table_declares(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not "agrees with today": the tool's set *is* the declaration."""
    from docmax.tools.convert.tool import SPEC

    declared = next(param for param in SPEC.params if param.name == "to")

    assert tuple(declared.choices) == _formats.convertible_names()


def test_to_images_offers_exactly_what_the_table_declares() -> None:
    from docmax.tools.to_images.tool import SPEC

    declared = next(param for param in SPEC.params if param.name == "format")

    assert tuple(declared.choices) == _formats.rasterisable_names()


def test_pdf_is_declared_rather_than_omitted() -> None:
    """So `--to pdf` can explain instead of reporting an unknown format."""
    pdf = _formats.document("pdf")

    assert pdf.readable is False
    assert pdf.writable is False
    assert pdf.unavailable_note is not None


def test_txt_is_write_only() -> None:
    txt = _formats.document("txt")

    assert txt.writable is True
    assert txt.readable is False


def test_no_convertible_format_needs_an_external_pdf_engine() -> None:
    """ADR 0011: M5 adds no second binary, so nothing here may require one."""
    assert "pdf" not in _formats.convertible_names()


def test_only_pandoc_and_poppler_are_needed_by_the_m5_tools() -> None:
    """The enforcement for "no second binary": `doctor` renders this list."""
    needed = {
        binary.name
        for binary in _binaries.EXTERNAL_BINARIES
        for tool in binary.used_by
        if tool in M5
    }

    assert needed == {"pandoc", "pdftoppm"}


@pytest.mark.parametrize("name", ["md", "docx", "png", "jpeg"])
def test_a_format_is_recognised_by_name_and_by_suffix(name: str) -> None:
    lookup = _formats.document if name in {"md", "docx"} else _formats.image
    by_name = lookup(name)

    assert lookup(by_name.suffix).name == name
    assert lookup(name.upper()).name == name


def test_every_image_format_declares_a_signature() -> None:
    """The header check is the whole point of the type; a format with none is a hole."""
    for image_format in _formats.IMAGE_FORMATS:
        assert image_format.signatures, f"{image_format.name} declares no signature"


def test_a_signature_rejects_the_wrong_bytes() -> None:
    png = _formats.image("png")

    assert png.matches(b"\x89PNG\r\n\x1a\nrest")
    assert not png.matches(b"\xff\xd8\xffrest")


def test_an_unknown_format_names_the_formats_command() -> None:
    """The remedy that `UnsupportedFormatError` has promised since M0."""
    with pytest.raises(InvalidParameterError) as caught:
        _formats.document("mediawiki")

    assert "formats" in (caught.value.remedy or "")


# ---------------------------------------------------------------------------
# The version probe
# ---------------------------------------------------------------------------
#
# Both binary-backed tools ask their binary for a version after the work is
# done, and report it as `engine_version`. That probe runs a second subprocess
# with a bare `-v`, which is easy to forget when writing a fake — and a fake
# that writes a file for every invocation then writes one named after the flag,
# in the working directory. These pin both halves: the probe is reported, and it
# leaves nothing behind.


def test_convert_reports_the_engine_that_did_the_work(
    router: EngineRouter, notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PANDOC_WRITES_OUTPUT)

    result = run(router, "convert", notes, tmp_path / "out.html", to="html")

    assert result.engine_version is not None
    assert result.engine_version.startswith("pandoc/")


def test_to_images_reports_the_engine_that_did_the_work(
    router: EngineRouter, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch, tmp_path, PDFTOPPM_WRITES_IMAGES)

    result = run(router, "to-images", source, tmp_path / "pages")

    assert result.engine_version is not None
    assert result.engine_version.startswith("pdftoppm/")


def test_a_render_writes_nothing_outside_its_output_directory(
    router: EngineRouter,
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test that writes into the repository is a bug in the test, not a quirk.

    The working directory is asserted unchanged because that is where the damage
    landed: the version probe's `-v` reached a fake that wrote `args[-1] + ext`,
    producing `-v.png` next to `pyproject.toml`.
    """
    install_fake(
        monkeypatch,
        tmp_path,
        'open(args[-1] + ".png", "wb").write(b"\\x89PNG\\r\\n\\x1a\\n")\n',
    )
    cwd = Path.cwd()
    before = set(cwd.iterdir())

    run(router, "to-images", source, tmp_path / "pages")

    assert set(cwd.iterdir()) == before, "the run wrote outside its output directory"
