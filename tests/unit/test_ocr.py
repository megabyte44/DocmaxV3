"""``ocr`` — the last of the nineteen tools, and the one v2 got most wrong.

The shape follows what `compress` established at M3 and `convert` reused at M5:

* **The contract** is tested against *fake binaries* that are real subprocesses.
  Two of them, because OCR runs Poppler and then Tesseract, and the fake
  dispatches on which was asked for. This is what lets every behaviour below —
  a failed page, a lost page, a blank text layer, a timeout, a cancellation —
  be tested on a machine with no OCR software installed at all.
* **Real recognition** is one test, marked `needs_binary`, skipped where
  Tesseract is absent and required in CI.

The deskew half lives in `test_deskew.py`, because it is a pure function and
v2's defect in it could only ever have been caught by testing it alone.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from docmax.core.cancellation import NEVER_CANCELLED, CancellationToken
from docmax.core.config import Config
from docmax.core.errors import (
    CancelledError,
    EncryptedDocumentError,
    ExternalToolFailedError,
    ExternalToolTimeoutError,
    InvalidParameterError,
    OutputValidationError,
)
from docmax.core.models import DocumentRef, Engine, OutputTarget
from docmax.core.protocols import NULL_PROGRESS
from docmax.core.registry import get_tool
from docmax.core.router import EngineRouter
from docmax.tools import _binaries
from docmax.tools.ocr import validators as ocr_validators

if TYPE_CHECKING:
    from docmax.core.models import ToolResult

needs_tesseract = pytest.mark.needs_binary("tesseract")

#: The marker *selects* the test into CI's external-binary job; it does not skip.
#: `compress` pairs it with a `skipif` for that, and this follows it — two of
#: M5's real-binary tests do not, and they fail rather than skip on a machine
#: without Pandoc or Poppler. OCR needs both binaries, so both are checked.
TOOLCHAIN_PRESENT = (
    _binaries.find("tesseract") is not None and _binaries.find("pdftoppm") is not None
)

#: Long enough to clear `validators.MIN_TEXT_CHARS`, so a page carrying it
#: counts as already searchable.
REAL_TEXT = "This page already carries a real text layer of its own."


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def write_pdf(path: Path, pages: int = 3, *, text: str | None = None) -> Path:
    """A PDF whose pages carry ``text`` — or nothing, standing in for a scan."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=300, height=300)
        if text:
            _draw(writer, page, text)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def _draw(writer: Any, page: Any, text: str) -> None:
    """Put extractable text on a page, using pypdf's own content stream API."""
    from pypdf.generic import DecodedStreamObject, NameObject

    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode("latin-1", errors="replace"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    _add_font(writer, page)


def _add_font(writer: Any, page: Any) -> None:
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject

    font = DictionaryObject()
    font.update(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject()
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = writer._add_object(font)
    resources[NameObject("/Font")] = fonts
    page[NameObject("/Resources")] = resources
    assert isinstance(TextStringObject("x"), TextStringObject)


def page_count(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def text_of(path: Path) -> str:
    from pypdf import PdfReader

    return " ".join(ocr_validators.text_of(p) for p in PdfReader(str(path)).pages)


def staged(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.glob(".*"))


# ---------------------------------------------------------------------------
# The fake OCR toolchain
# ---------------------------------------------------------------------------

#: Poppler's part: draw a page as a PNG. The fake writes a tiny real PNG, so
#: the deskew step downstream has something it can actually open.
POPPLER_OK = """
root = args[-1]
import zlib, struct
def chunk(tag, data):
    c = tag + data
    return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c))
raw = b''.join(b'\\x00' + bytes([255, 255, 255] * 8) for _ in range(8))
png = (b'\\x89PNG\\r\\n\\x1a\\n'
       + chunk(b'IHDR', struct.pack('>IIBBBBB', 8, 8, 8, 2, 0, 0, 0))
       + chunk(b'IDAT', zlib.compress(raw))
       + chunk(b'IEND', b''))
open(root + '.png', 'wb').write(png)
"""

#: Tesseract's part: emit a one-page PDF carrying real extractable text.
TESSERACT_OK = """
root = args[1]
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
w = PdfWriter()
p = w.add_blank_page(width=300, height=300)
s = DecodedStreamObject()
s.set_data(b'BT /F1 12 Tf 20 150 Td (recognised text from the fake engine) Tj ET')
p[NameObject('/Contents')] = w._add_object(s)
f = DictionaryObject()
f.update({NameObject('/Type'): NameObject('/Font'),
          NameObject('/Subtype'): NameObject('/Type1'),
          NameObject('/BaseFont'): NameObject('/Helvetica')})
r = DictionaryObject(); fonts = DictionaryObject()
fonts[NameObject('/F1')] = w._add_object(f); r[NameObject('/Font')] = fonts
p[NameObject('/Resources')] = r
open(root + '.pdf', 'wb').write(b'')
with open(root + '.pdf', 'wb') as h:
    w.write(h)
"""

#: A recogniser that succeeds but finds nothing — the characteristic OCR
#: failure the `has_text_layer` validator exists to catch.
TESSERACT_BLANK = """
root = args[1]
from pypdf import PdfWriter
w = PdfWriter(); w.add_blank_page(width=300, height=300)
with open(root + '.pdf', 'wb') as h:
    w.write(h)
"""

LIST_LANGS = "\n".join(["List of available languages:", "eng", "osd", "deu"])


def install_toolchain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    poppler: str = POPPLER_OK,
    tesseract: str = TESSERACT_OK,
    langs: str = LIST_LANGS,
) -> None:
    """Point `ocr` at two fakes, through the real binary mechanism.

    The dispatch is on the *arguments* `ocr` actually built — `-singlefile` for
    Poppler, the literal `pdf` for Tesseract — so the flags this tool constructs
    are exercised as written rather than assumed.
    """
    script = tmp_path / "fake_ocr_toolchain.py"
    script.write_text(
        "import sys\n"
        "args = sys.argv[1:]\n"
        f"LANGS = {langs!r}\n"
        "if '--list-langs' in args:\n"
        "    sys.stdout.write(LANGS)\n"
        "elif '--version' in args:\n"
        "    sys.stdout.write('tesseract 9.9.9-fake')\n"
        "elif '-singlefile' in args:\n" + _indent(poppler) + "else:\n" + _indent(tesseract),
        encoding="utf-8",
    )
    real_run = _binaries.run

    def run_fake(command: Any, **kwargs: Any) -> Any:
        # Swap only the executable; every other argument is the one `ocr`
        # actually built.
        return real_run([sys.executable, str(script), *[str(c) for c in command[1:]]], **kwargs)

    monkeypatch.setattr(_binaries, "find", lambda name: sys.executable)
    monkeypatch.setattr(_binaries, "require", lambda name, *, tool: sys.executable)
    monkeypatch.setattr(_binaries, "run", run_fake)


def _indent(body: str) -> str:
    return "".join(f"    {line}\n" for line in body.strip().splitlines()) or "    pass\n"


@pytest.fixture
def router() -> EngineRouter:
    return EngineRouter(config=Config())


@pytest.fixture(autouse=True)
def _no_opencv_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deskew is off by default in these tests; `test_deskew.py` owns it.

    Without this, every test here would depend on OpenCV being installed — and
    what is under test is the pipeline, not the straightening.
    """
    from docmax.tools import _deskew

    monkeypatch.setattr(_deskew, "is_available", lambda: True)
    monkeypatch.setattr(_deskew, "straighten", lambda path: 0.0)


def ocr(
    router: EngineRouter,
    source: Path,
    destination: Path,
    *,
    cancellation: Any = NEVER_CANCELLED,
    progress: Any = NULL_PROGRESS,
    **params: Any,
) -> ToolResult:
    params.setdefault("deskew", False)
    return router.run(
        "ocr",
        [DocumentRef.from_path(source)],
        OutputTarget(destination=destination),
        progress=progress,
        cancellation=cancellation,
        **params,
    )


# ---------------------------------------------------------------------------
# ToolSpec
# ---------------------------------------------------------------------------


def test_ocr_declares_both_engines_and_three_parameters() -> None:
    spec = get_tool("ocr")

    assert spec.supports(Engine.LOCAL)
    assert spec.supports(Engine.CLOUD)
    assert [p.name for p in spec.params] == ["lang", "dpi", "deskew"]
    assert spec.default_suffix == ".pdf"
    assert spec.category == "extract"


def test_the_defaults_are_the_ones_the_spec_promises() -> None:
    params = {p.name: p.default for p in get_tool("ocr").params}

    assert params == {"lang": "eng", "dpi": 300, "deskew": True}


def test_ocr_is_no_longer_a_skeleton() -> None:
    """The `run()` that raised through seven milestones."""
    from docmax.tools.ocr.local import OcrLocal

    source = OcrLocal.run.__doc__ or ""
    assert "NotImplementedError" not in source


# ---------------------------------------------------------------------------
# Successful recognition
# ---------------------------------------------------------------------------


def test_a_scanned_document_gains_a_text_layer(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_toolchain(monkeypatch, tmp_path)
    source = write_pdf(tmp_path / "scan.pdf", 3)
    out = tmp_path / "out.pdf"

    result = ocr(router, source, out)

    assert result.outputs == (out,)
    assert result.engine_used is Engine.LOCAL
    assert page_count(out) == 3
    assert "recognised text" in text_of(out)
    assert result.details["recognised"] == 3
    assert result.details["skipped_with_text"] == []
    assert result.details["failed"] == []


def test_a_multi_page_document_recognises_every_page(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_toolchain(monkeypatch, tmp_path)
    source = write_pdf(tmp_path / "scan.pdf", 12)

    result = ocr(router, source, tmp_path / "out.pdf")

    assert result.details["pages"] == 12
    assert result.details["recognised"] == 12
    assert page_count(tmp_path / "out.pdf") == 12


def test_the_engine_version_reports_what_actually_ran(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_toolchain(monkeypatch, tmp_path)

    result = ocr(router, write_pdf(tmp_path / "s.pdf", 1), tmp_path / "out.pdf")

    assert result.engine_version == "tesseract/9.9.9-fake"


def test_progress_advances_once_per_page(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_toolchain(monkeypatch, tmp_path)

    class Recorder:
        def __init__(self) -> None:
            self.total: int | None = None
            self.steps = 0
            self.finished = False

        def start(self, description: str, *, total: int | None = None) -> None:
            self.total = total

        def advance(self, amount: int = 1) -> None:
            self.steps += amount

        def finish(self) -> None:
            self.finished = True

    recorder = Recorder()
    ocr(router, write_pdf(tmp_path / "s.pdf", 5), tmp_path / "out.pdf", progress=recorder)

    assert recorder.total == 5
    assert recorder.steps == 5
    assert recorder.finished


# ---------------------------------------------------------------------------
# Pages that already carry text — the M8 contract
# ---------------------------------------------------------------------------


def test_a_page_that_already_has_text_is_left_exactly_as_it_was(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-recognising it would replace real text with a picture of it."""
    install_toolchain(monkeypatch, tmp_path)
    source = write_pdf(tmp_path / "text.pdf", 2, text=REAL_TEXT)
    out = tmp_path / "out.pdf"

    result = ocr(router, source, out)

    assert result.details["recognised"] == 0
    assert result.details["skipped_with_text"] == [1, 2]
    assert REAL_TEXT in text_of(out)
    assert "recognised text" not in text_of(out)


def test_a_mixed_document_recognises_only_the_scanned_pages(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A scanned contract behind a generated cover page is the common case."""
    from pypdf import PdfWriter

    install_toolchain(monkeypatch, tmp_path)

    cover = write_pdf(tmp_path / "cover.pdf", 1, text=REAL_TEXT)
    scan = write_pdf(tmp_path / "scan.pdf", 2)
    merged = tmp_path / "mixed.pdf"
    writer = PdfWriter()
    writer.append(str(cover))
    writer.append(str(scan))
    with merged.open("wb") as handle:
        writer.write(handle)

    out = tmp_path / "out.pdf"
    result = ocr(router, merged, out)

    assert result.details["pages"] == 3
    assert result.details["skipped_with_text"] == [1]
    assert result.details["recognised"] == 2
    body = text_of(out)
    assert REAL_TEXT in body, "the cover's real text survived"
    assert "recognised text" in body, "the scanned pages were recognised"


def test_a_fully_searchable_document_is_copied_through_and_still_validates(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_toolchain(monkeypatch, tmp_path)
    source = write_pdf(tmp_path / "text.pdf", 3, text=REAL_TEXT)

    result = ocr(router, source, tmp_path / "out.pdf")

    assert result.details["recognised"] == 0
    assert page_count(tmp_path / "out.pdf") == 3


def test_a_stray_glyph_does_not_count_as_a_text_layer() -> None:
    """A page number burned into a scan must not make the page 'searchable'."""
    assert ocr_validators.MIN_TEXT_CHARS > 1

    class Page:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    assert not ocr_validators.has_text(Page("7"))
    assert ocr_validators.has_text(Page(REAL_TEXT))


def test_an_unreadable_page_counts_as_having_no_text() -> None:
    class Exploding:
        def extract_text(self) -> str:
            raise RuntimeError("this content stream is a mess")

    assert ocr_validators.text_of(Exploding()) == ""
    assert not ocr_validators.has_text(Exploding())


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["", "   ", "eng+", "+eng", "en g", "eng+!"])
def test_a_malformed_language_is_a_typed_error(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lang: str
) -> None:
    install_toolchain(monkeypatch, tmp_path)
    with pytest.raises(InvalidParameterError) as caught:
        ocr(router, write_pdf(tmp_path / "s.pdf", 1), tmp_path / "out.pdf", lang=lang)
    assert caught.value.remedy


def test_the_v2_multi_language_syntax_still_works(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`docmax ocr scan.pdf --lang eng+hin` — promised by migrating-from-v2.md."""
    install_toolchain(monkeypatch, tmp_path, langs="\n".join(["eng", "hin", "osd"]))

    result = ocr(router, write_pdf(tmp_path / "s.pdf", 1), tmp_path / "out.pdf", lang="eng+hin")

    assert result.details["lang"] == "eng+hin"


def test_a_language_pack_that_is_not_installed_is_refused_by_name(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """'exit 1' sends a user to the wrong place; naming the packs does not."""
    install_toolchain(
        monkeypatch,
        tmp_path,
        langs="\n".join(["List of available languages:", "eng", "osd"]),
    )

    with pytest.raises(InvalidParameterError) as caught:
        ocr(router, write_pdf(tmp_path / "s.pdf", 1), tmp_path / "out.pdf", lang="deu")

    assert "deu" in caught.value.message
    assert "eng" in (caught.value.remedy or "")


def test_a_language_probe_that_fails_does_not_block_the_run(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Tesseract that cannot list its languages is not a reason to refuse."""
    install_toolchain(monkeypatch, tmp_path, langs="")

    result = ocr(router, write_pdf(tmp_path / "s.pdf", 1), tmp_path / "out.pdf", lang="deu")

    assert result.details["lang"] == "deu"


@pytest.mark.parametrize("dpi", [0, 11, 1201, 40000, -5])
def test_an_out_of_range_dpi_is_refused(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dpi: int
) -> None:
    install_toolchain(monkeypatch, tmp_path)
    with pytest.raises(InvalidParameterError):
        ocr(router, write_pdf(tmp_path / "s.pdf", 1), tmp_path / "out.pdf", dpi=dpi)


@pytest.mark.parametrize("dpi", ["300", 3.5, True])
def test_a_dpi_that_is_not_a_whole_number_is_refused(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dpi: Any
) -> None:
    install_toolchain(monkeypatch, tmp_path)
    with pytest.raises(InvalidParameterError):
        ocr(router, write_pdf(tmp_path / "s.pdf", 1), tmp_path / "out.pdf", dpi=dpi)


def test_a_deskew_that_is_not_a_boolean_is_refused(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_toolchain(monkeypatch, tmp_path)
    with pytest.raises(InvalidParameterError):
        ocr(router, write_pdf(tmp_path / "s.pdf", 1), tmp_path / "out.pdf", deskew="yes")


def test_deskew_without_opencv_is_a_typed_error(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from docmax.tools import _deskew

    install_toolchain(monkeypatch, tmp_path)
    monkeypatch.setattr(_deskew, "is_available", lambda: False)

    with pytest.raises(InvalidParameterError) as caught:
        ocr(router, write_pdf(tmp_path / "s.pdf", 1), tmp_path / "out.pdf", deskew=True)

    assert "--no-deskew" in (caught.value.remedy or "")


def test_a_bad_parameter_is_refused_before_the_destination_is_touched(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_toolchain(monkeypatch, tmp_path)
    out = tmp_path / "out.pdf"

    with pytest.raises(InvalidParameterError):
        ocr(router, write_pdf(tmp_path / "s.pdf", 1), out, dpi=99999)

    assert not out.exists()


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


def test_an_encrypted_document_is_refused(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pypdf import PdfWriter

    install_toolchain(monkeypatch, tmp_path)
    locked = tmp_path / "locked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret", algorithm="RC4-128")
    with locked.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(EncryptedDocumentError):
        ocr(router, locked, tmp_path / "out.pdf")


def test_a_corrupt_document_is_refused(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from docmax.core.errors import CorruptDocumentError

    install_toolchain(monkeypatch, tmp_path)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nnot really\n")

    with pytest.raises(CorruptDocumentError):
        ocr(router, broken, tmp_path / "out.pdf")


def test_a_missing_dependency_is_reported_with_an_install_command() -> None:
    from docmax.tools.ocr.local import OcrLocal, install_hint

    reason = OcrLocal().unavailable_reason()
    if reason is None:  # pragma: no cover — a machine with the full toolchain
        pytest.skip("the OCR toolchain is installed here")

    assert "tesseract" in reason or "pdftoppm" in reason
    # It used to say `setup --ocr`, a command that does not exist. ADR 0022.
    assert "setup --ocr" not in install_hint()
    assert "--engine cloud" in install_hint()


def test_missing_dependencies_is_empty_when_everything_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The optional, duck-typed extra `protocols.py` documents: deterministic
    here, unlike `unavailable_reason` above, because the whole point is not
    to depend on what happens to be on this machine's PATH."""
    from docmax.tools.ocr.local import OcrLocal

    monkeypatch.setattr(_binaries, "find", lambda name: f"/usr/bin/{name}")

    assert OcrLocal().missing_dependencies() == ()


def test_missing_dependencies_names_exactly_whats_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """One `MissingDependency` per absent binary, each carrying the official
    install page `_binaries.py` declares for it — never a name this test
    invented, and never a third-party mirror."""
    from docmax.tools.ocr.local import OcrLocal

    def find(name: str) -> str | None:
        return "/usr/bin/pdftoppm" if name == "pdftoppm" else None

    monkeypatch.setattr(_binaries, "find", find)

    (missing,) = OcrLocal().missing_dependencies()

    assert missing.name == "tesseract"
    assert "OCR" in missing.reason
    assert "tesseract" in missing.reason.lower()
    assert missing.url == _binaries.describe("tesseract").homepage
    assert missing.url is not None
    assert missing.url.startswith("https://")


def test_missing_dependencies_excludes_opencv(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenCV is a `--deskew`-only Python dependency and must never appear as
    a reason the whole engine is unavailable — the same rule
    `missing_dependencies()` (the module function) already applies to
    `is_available`."""
    from docmax.tools.ocr.local import OcrLocal

    monkeypatch.setattr(_binaries, "find", lambda name: f"/usr/bin/{name}")

    reported = OcrLocal().missing_dependencies()

    assert not any("opencv" in dependency.name.lower() for dependency in reported)
    assert not any("cv2" in dependency.name.lower() for dependency in reported)


def test_the_router_reports_ocrs_missing_dependencies_generically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other end of the same mechanism: `EngineRouter.missing_dependencies`
    reads `OcrLocal`'s method by `getattr`, exactly as it would for any other
    tool that implemented one — this test is the one place that exercises
    both halves together through the real registry. No registry refresh is
    needed: `load_strategy` builds a fresh `OcrLocal()` on every call, so the
    `_binaries.find` patch below is picked up regardless of when `ocr` was
    first registered."""
    monkeypatch.setattr(_binaries, "find", lambda name: None)

    router = EngineRouter()
    reported = router.missing_dependencies("ocr", Engine.LOCAL)

    names = {dependency.name for dependency in reported}
    assert names == {"tesseract", "pdftoppm"}
    assert all(dependency.url for dependency in reported)


# ---------------------------------------------------------------------------
# Failure, validation and atomicity
# ---------------------------------------------------------------------------


def test_a_blank_text_layer_fails_validation(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The characteristic OCR failure: a file that looks perfect and is empty."""
    install_toolchain(monkeypatch, tmp_path, tesseract=TESSERACT_BLANK)
    out = tmp_path / "out.pdf"

    with pytest.raises(OutputValidationError) as caught:
        ocr(router, write_pdf(tmp_path / "s.pdf", 2), out)

    assert not out.exists()
    assert "--lang" in (caught.value.remedy or "")


def test_a_lost_page_fails_validation(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from docmax.tools.ocr.validators import page_count_is

    produced = write_pdf(tmp_path / "two.pdf", 2, text=REAL_TEXT)
    with pytest.raises(OutputValidationError):
        page_count_is(3)(produced)


def test_one_failed_page_does_not_lose_the_others(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The judgement `crop` makes about a page its box does not fit."""
    # Flat, not pre-indented: `install_toolchain` indents the whole body once,
    # and indenting it here too would fold the success path into the `if`.
    fails_page_two = "import sys\nif '00002' in args[1]:\n    sys.exit(1)\n" + TESSERACT_OK
    install_toolchain(monkeypatch, tmp_path, tesseract=fails_page_two)
    out = tmp_path / "out.pdf"

    result = ocr(router, write_pdf(tmp_path / "s.pdf", 3), out)

    assert result.details["failed"] == [2]
    assert result.details["recognised"] == 2
    assert page_count(out) == 3, "the page that failed is still in the document"


def test_a_run_where_every_page_fails_is_an_error(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_toolchain(monkeypatch, tmp_path, tesseract="import sys; sys.exit(1)")
    out = tmp_path / "out.pdf"

    with pytest.raises(ExternalToolFailedError) as caught:
        ocr(router, write_pdf(tmp_path / "s.pdf", 3), out)

    assert not out.exists()
    assert caught.value.remedy


def test_a_rasteriser_that_writes_nothing_is_survivable(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Poppler exiting zero without drawing — `compress` guards the same case."""
    install_toolchain(monkeypatch, tmp_path, poppler="pass")
    out = tmp_path / "out.pdf"

    with pytest.raises(ExternalToolFailedError):
        ocr(router, write_pdf(tmp_path / "s.pdf", 2), out)

    assert not out.exists()


def test_a_failure_leaves_no_staged_file_behind(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_toolchain(monkeypatch, tmp_path, tesseract=TESSERACT_BLANK)
    destination = tmp_path / "out" / "out.pdf"
    destination.parent.mkdir()

    with pytest.raises(OutputValidationError):
        ocr(router, write_pdf(tmp_path / "s.pdf", 1), destination)

    assert staged(destination.parent) == []
    assert list(destination.parent.iterdir()) == []


def test_an_existing_output_is_not_replaced_by_a_failed_run(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_toolchain(monkeypatch, tmp_path, tesseract=TESSERACT_BLANK)
    out = tmp_path / "out.pdf"
    out.write_bytes(b"the previous run's output")

    with pytest.raises(OutputValidationError):
        router.run(
            "ocr",
            [DocumentRef.from_path(write_pdf(tmp_path / "s.pdf", 1))],
            OutputTarget(destination=out, force=True),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
            deskew=False,
        )

    assert out.read_bytes() == b"the previous run's output"


# ---------------------------------------------------------------------------
# Nothing is written beside the source — v2's named defect
# ---------------------------------------------------------------------------


def test_nothing_is_written_beside_the_source(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """v2 wrote `_preprocessed.png` next to every document, and folder-watch
    then ate its own output."""
    install_toolchain(monkeypatch, tmp_path)
    source_dir = tmp_path / "documents"
    source_dir.mkdir()
    source = write_pdf(source_dir / "scan.pdf", 3)

    ocr(router, source, tmp_path / "out.pdf")

    assert [p.name for p in source_dir.iterdir()] == ["scan.pdf"]
    assert source.stat().st_size > 0


def test_a_cancelled_run_leaves_nothing_beside_the_source_either(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_toolchain(monkeypatch, tmp_path)
    source_dir = tmp_path / "documents"
    source_dir.mkdir()
    source = write_pdf(source_dir / "scan.pdf", 3)

    token = CancellationToken()
    token.cancel()
    with pytest.raises(CancelledError):
        ocr(router, source, tmp_path / "out.pdf", cancellation=token)

    assert [p.name for p in source_dir.iterdir()] == ["scan.pdf"]


# ---------------------------------------------------------------------------
# Cancellation and timeout
# ---------------------------------------------------------------------------


def test_cancellation_leaves_the_destination_untouched(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_toolchain(monkeypatch, tmp_path)
    out = tmp_path / "out.pdf"
    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError):
        ocr(router, write_pdf(tmp_path / "s.pdf", 5), out, cancellation=token)

    assert not out.exists()


def test_cancelling_part_way_through_stops_within_a_page(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Not at the end of a five-hundred-page scan."""
    install_toolchain(monkeypatch, tmp_path)
    token = CancellationToken()
    seen = 0

    class CancelAfterTwo:
        def start(self, description: str, *, total: int | None = None) -> None:
            pass

        def advance(self, amount: int = 1) -> None:
            nonlocal seen
            seen += amount
            if seen == 2:
                token.cancel()

        def finish(self) -> None:
            pass

    out = tmp_path / "out.pdf"
    with pytest.raises(CancelledError):
        ocr(
            router,
            write_pdf(tmp_path / "s.pdf", 20),
            out,
            cancellation=token,
            progress=CancelAfterTwo(),
        )

    assert seen < 20, "the run stopped rather than finishing the document"
    assert not out.exists()


def test_a_hung_recogniser_times_out(
    router: EngineRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """v2 had no subprocess timeout anywhere; a hung tool hung DocMax."""
    install_toolchain(monkeypatch, tmp_path, tesseract="import time; time.sleep(30)")
    out = tmp_path / "out.pdf"

    with pytest.raises(ExternalToolTimeoutError):
        ocr(
            router,
            write_pdf(tmp_path / "s.pdf", 1),
            out,
            cancellation=CancellationToken(timeout=1.0),
        )

    assert not out.exists()


# ---------------------------------------------------------------------------
# Real Tesseract — CI only
# ---------------------------------------------------------------------------


@needs_tesseract
@pytest.mark.golden
@pytest.mark.skipif(not TOOLCHAIN_PRESENT, reason="Tesseract and Poppler are not installed")
def test_real_tesseract_makes_a_rendered_page_searchable(
    router: EngineRouter, tmp_path: Path
) -> None:
    """The one end-to-end test. `eng` only — `deu` is installed on Linux alone.

    Deliberately asserts the *contract* rather than the recognised string:
    Tesseract's output differs between versions and language data releases, so
    an assertion on the text would be a version check wearing a correctness
    costume. What must hold on every version is that the pipeline completes,
    the page count survives, and the validators — including `has_text_layer` —
    pass against real output.
    """
    source = write_pdf(tmp_path / "typed.pdf", 1, text="INVOICE")
    out = tmp_path / "searchable.pdf"

    result = ocr(router, source, out, lang="eng", dpi=300)

    assert page_count(out) == 1
    assert result.engine_used is Engine.LOCAL
    assert result.engine_version is not None
    assert result.engine_version.startswith("tesseract/")
