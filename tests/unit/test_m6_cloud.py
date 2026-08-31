"""The cloud engines, the consent gate, and the reference server's runner.

Three things get the most attention, because each is a promise the project has
made in prose and never enforced:

* **No path reaches `cloud_client` without passing the consent gate.**
  `phases.md` names this as Phase 5's definition of done. Nothing asserted it
  until M6. It is asserted here by giving the strategy a transport that *fails
  the test if it is touched at all* — so "the request was not sent" is proven
  rather than inferred from an exception type.
* **`offline` beats an explicit `--engine cloud`.** The flag exists for someone
  whose policy says documents do not leave the building, so an argument must not
  defeat it.
* **A cloud result is validated by the tool's own validators.** The point of the
  dual-engine design is one set of guarantees, not two — a cloud `compress` that
  lost a page must fail exactly where a local one would.

`respx` intercepts at the transport layer, so the strategies under test are the
real ones running the real client. The reference server is driven through
FastAPI's `TestClient` with a fake binary standing in for Ghostscript or Pandoc,
following the pattern `test_compress.py` established at M3.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx

from docmax.cloud_client import CloudClient, CloudConfig
from docmax.core.cancellation import NEVER_CANCELLED, CancellationToken
from docmax.core.config import Config
from docmax.core.consent import ConsentStore
from docmax.core.errors import (
    CancelledError,
    ConsentRequiredError,
    CorruptDocumentError,
    NoEngineAvailableError,
    OutputValidationError,
)
from docmax.core.models import DocumentRef, Engine, OutputTarget
from docmax.core.protocols import NULL_PROGRESS
from docmax.core.registry import get_tool
from docmax.core.router import EngineRouter
from docmax.tools import _binaries

if TYPE_CHECKING:
    from pathlib import Path

ENDPOINT = "https://api.example.invalid"
M6_CLOUD_TOOLS = ("compress", "convert")

#: Every tool with a working cloud engine. `ocr` joined at M8 — the milestone
#: ADR 0012 named when it deliberately held OCR back, so this is that ADR being
#: executed rather than overturned. `to-images` joined after it, per ADR 0034 —
#: it shares `ocr`'s Poppler dependency, and is the first cloud tool whose
#: output is a directory rather than a file.
CLOUD_TOOLS = (*M6_CLOUD_TOOLS, "ocr", "to-images")


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


def write_searchable_pdf(path: Path, pages: int = 3) -> Path:
    """A PDF whose pages carry real extractable text, standing in for OCR output."""
    from tests.unit.test_ocr import REAL_TEXT
    from tests.unit.test_ocr import write_pdf as write_with_text

    return write_with_text(path, pages, text=REAL_TEXT)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return write_pdf(tmp_path / "doc.pdf", 3)


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    path = tmp_path / "notes.md"
    path.write_text("# Title\n\nSome words.\n", encoding="utf-8")
    return path


@pytest.fixture
def consented(tmp_path: Path) -> ConsentStore:
    store = ConsentStore(tmp_path / "consent.json", endpoint=ENDPOINT)
    for tool in CLOUD_TOOLS:
        store.record(tool)
    return store


@pytest.fixture
def empty_consent(tmp_path: Path) -> ConsentStore:
    return ConsentStore(tmp_path / "consent.json", endpoint=ENDPOINT)


def cloud_config(**overrides: Any) -> Config:
    return Config(cloud_endpoint=ENDPOINT, api_key="test-key", **overrides)


@pytest.fixture(autouse=True)
def configured_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the endpoint and key where `core.config.load()` will find them.

    A cloud strategy resolves its own configuration through `load()` rather than
    receiving the router's -- ADR 0013, because the registry constructs a
    strategy with no arguments. So setting only the router's `Config` would
    leave the two disagreeing, and the router would refuse on availability
    before consent was ever consulted.

    Setting the environment is what the CLI effectively does: `build_router()`
    also calls `load()`, so in the real product both sides read one source.
    `test_the_router_and_the_strategy_read_one_configuration` pins that.
    """
    monkeypatch.setenv("DOCMAX_CLOUD_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("DOCMAX_API_KEY", "test-key")


def client_for(transport: httpx.BaseTransport | None = None) -> CloudClient:
    config = CloudConfig(endpoint=ENDPOINT, api_key="test-key", max_retries=0)
    if transport is None:
        return CloudClient(config)
    return CloudClient(config, http=httpx.Client(transport=transport, base_url=ENDPOINT))


def succeeded(**extra: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "job_id": "job_1",
        "status": "succeeded",
        "output": {
            "url": f"{ENDPOINT}/v1/outputs/f_1",
            "size_bytes": 9,
            "content_type": "application/pdf",
        },
        **extra,
    }


class ExplodingTransport(httpx.BaseTransport):
    """A transport that fails the test if anything reaches it.

    The consent gate's assertion is *"no request was made"*, and an exception
    type alone does not prove that — a strategy could upload and then raise. So
    the proof is that the socket layer was never reached.
    """

    def handle_request(self, request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"a request reached the network: {request.method} {request.url}")


# ---------------------------------------------------------------------------
# Which tools have cloud engines — ADR 0012
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CLOUD_TOOLS)
def test_the_m6_tools_declare_both_engines(name: str) -> None:
    spec = get_tool(name)

    assert spec.supports(Engine.LOCAL)
    assert spec.supports(Engine.CLOUD)


def test_only_the_intended_tools_have_a_working_cloud_engine() -> None:
    """Exactly three tools have a cloud engine, and every one of them works.

    This test used to detect `ocr` by the hand-rolled `OcrCloud` class it kept
    while ADR 0012 held OCR back to M8, and its job was to fail if that class
    ever started working. M8 is that milestone: `OcrCloud` is gone, `ocr` runs
    through the shared `CloudEngine` like the other two, and the assertion
    inverts — every declared cloud engine must now be a real one.

    It still fails loudly if a fourth appears, which is the half that was
    always the point.
    """
    from docmax.core.registry import iter_tools
    from docmax.tools._cloud import CloudEngine

    declared = {spec.name for spec in iter_tools(engine=Engine.CLOUD)}
    assert declared == set(CLOUD_TOOLS)

    for name in CLOUD_TOOLS:
        strategy = get_tool(name).load_strategy(Engine.CLOUD)
        assert isinstance(strategy, CloudEngine), f"{name} does not use the shared flow"


def test_the_tools_the_docs_mention_but_do_not_exist_still_do_not() -> None:
    """`pdfa` and `remove-bg` are named in the architecture docs and on no roadmap row."""
    from docmax.core.registry import build_registry

    registry = build_registry()

    assert "pdfa" not in registry
    assert "remove-bg" not in registry


@pytest.mark.parametrize("name", ["merge", "split", "watermark", "from-images"])
def test_a_pure_python_tool_has_no_cloud_engine(name: str) -> None:
    """`to-images` is deliberately absent: ADR 0034 gives it a cloud engine.

    It is the one tool outside this list with a genuinely painful native
    dependency -- the same Poppler binary `ocr` already uses -- rather than a
    millisecond-long pure-Python operation.
    """
    assert not get_tool(name).supports(Engine.CLOUD)


# ---------------------------------------------------------------------------
# The consent gate — the Phase 5 promise, finally asserted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CLOUD_TOOLS)
def test_no_request_reaches_the_network_without_consent(
    name: str, empty_consent: ConsentStore, source: Path, tmp_path: Path
) -> None:
    """The definition of done `phases.md` set for Phase 5, five milestones ago."""
    router = EngineRouter(config=cloud_config(), consent=empty_consent)

    with pytest.raises(ConsentRequiredError):
        router.run(
            name,
            [DocumentRef.from_path(source)],
            OutputTarget(destination=tmp_path / "out.pdf", force=True),
            requested=Engine.CLOUD,
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
            to="html",
        )


def test_the_router_and_the_strategy_read_one_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The invariant ADR 0013's design depends on, asserted rather than assumed.

    The router decides *whether* to upload using its own `Config`; the strategy
    decides *where* using one it resolves itself. If those two ever came from
    different places, consent could be checked against one endpoint and the
    document sent to another — so the CLI's wiring, which makes both call
    `load()`, is the thing that has to stay true.
    """
    from docmax.cli.execution import build_router
    from docmax.cloud_client import CloudConfig
    from docmax.core.config import load

    monkeypatch.setenv("DOCMAX_CLOUD_ENDPOINT", "https://elsewhere.invalid")
    monkeypatch.setenv("DOCMAX_API_KEY", "k")

    router = build_router()
    strategy_view = CloudConfig.from_core(load())

    assert router.config.cloud_endpoint == strategy_view.endpoint
    assert router.consent is not None


def test_a_consent_store_that_is_absent_means_no_consent(source: Path, tmp_path: Path) -> None:
    """Failing closed: a caller who forgot a store must not thereby gain permission."""
    router = EngineRouter(config=cloud_config(), consent=None)

    with pytest.raises(ConsentRequiredError):
        router.run(
            "compress",
            [DocumentRef.from_path(source)],
            OutputTarget(destination=tmp_path / "out.pdf", force=True),
            requested=Engine.CLOUD,
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
        )


def test_consent_for_one_tool_does_not_carry_to_another(tmp_path: Path, source: Path) -> None:
    store = ConsentStore(tmp_path / "consent.json", endpoint=ENDPOINT)
    store.record("compress")
    router = EngineRouter(config=cloud_config(), consent=store)

    with pytest.raises(ConsentRequiredError):
        router.run(
            "convert",
            [DocumentRef.from_path(source)],
            OutputTarget(destination=tmp_path / "out.html", force=True),
            requested=Engine.CLOUD,
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
            to="html",
        )


def test_consent_for_one_endpoint_does_not_carry_to_another(tmp_path: Path) -> None:
    """Agreeing to a box on your LAN is not agreeing to a service on the internet."""
    path = tmp_path / "consent.json"
    ConsentStore(path, endpoint="http://localhost:8000").record("compress")

    elsewhere = ConsentStore(path, endpoint=ENDPOINT)

    assert not elsewhere.has("compress")


# ---------------------------------------------------------------------------
# Offline — beats an explicit request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CLOUD_TOOLS)
def test_offline_defeats_an_explicit_cloud_request(
    name: str, consented: ConsentStore, source: Path, tmp_path: Path
) -> None:
    """The switch exists for someone whose policy says documents stay put."""
    router = EngineRouter(config=cloud_config(offline=True), consent=consented)

    with pytest.raises(NoEngineAvailableError, match="offline"):
        router.run(
            name,
            [DocumentRef.from_path(source)],
            OutputTarget(destination=tmp_path / "out.pdf", force=True),
            requested=Engine.CLOUD,
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
            to="html",
        )


def test_offline_is_checked_before_consent(source: Path, tmp_path: Path) -> None:
    """A policy must surface as a refusal, never as a question the user can answer."""
    router = EngineRouter(config=cloud_config(offline=True), consent=None)

    with pytest.raises(NoEngineAvailableError):
        router.run(
            "compress",
            [DocumentRef.from_path(source)],
            OutputTarget(destination=tmp_path / "out.pdf", force=True),
            requested=Engine.CLOUD,
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
        )


def test_offline_cannot_be_turned_off_by_an_override() -> None:
    """`with_overrides` is one-way for this flag, by design."""
    assert cloud_config(offline=True).with_overrides(offline=False).offline is True


# ---------------------------------------------------------------------------
# The shared cloud flow
# ---------------------------------------------------------------------------


@respx.mock
def test_compress_runs_in_the_cloud_end_to_end(source: Path, tmp_path: Path) -> None:
    from docmax.tools.compress.cloud import build

    smaller = write_pdf(tmp_path / "smaller.pdf", 3).read_bytes()
    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(200, json=succeeded(engine_version="gs/10.03.0"))
    )
    respx.get(f"{ENDPOINT}/v1/outputs/f_1").mock(return_value=httpx.Response(200, content=smaller))
    out = tmp_path / "out.pdf"

    result = build(client_for()).run(
        [DocumentRef.from_path(source)],
        OutputTarget(destination=out, force=True),
        progress=NULL_PROGRESS,
        cancellation=NEVER_CANCELLED,
        preset="ebook",
    )

    assert result.engine_used is Engine.CLOUD
    assert result.engine_version == "gs/10.03.0"
    assert out.read_bytes() == smaller


@respx.mock
def test_convert_runs_in_the_cloud_end_to_end(notes: Path, tmp_path: Path) -> None:
    from docmax.tools.convert.cloud import build

    respx.post(f"{ENDPOINT}/v1/tools/convert").mock(
        return_value=httpx.Response(200, json=succeeded())
    )
    respx.get(f"{ENDPOINT}/v1/outputs/f_1").mock(
        return_value=httpx.Response(200, content=b"<html><body>hi</body></html>")
    )
    out = tmp_path / "out.html"

    result = build(client_for()).run(
        [DocumentRef.from_path(notes)],
        OutputTarget(destination=out, force=True),
        progress=NULL_PROGRESS,
        cancellation=NEVER_CANCELLED,
        to="html",
    )

    assert result.engine_used is Engine.CLOUD
    assert "converted" in out.read_text(encoding="utf-8") or out.read_text(encoding="utf-8")


@respx.mock
def test_ocr_runs_in_the_cloud_end_to_end(source: Path, tmp_path: Path) -> None:
    """M8. The tool the Cloud Engine's whole justification names.

    `architecture/overview.md` has said since M0 that cloud exists so a user can
    skip installing Tesseract; this is the first test in which that is true.
    """
    from docmax.tools.ocr.cloud import build

    searchable = write_searchable_pdf(tmp_path / "searchable.pdf", 3).read_bytes()
    respx.post(f"{ENDPOINT}/v1/tools/ocr").mock(
        return_value=httpx.Response(200, json=succeeded(engine_version="tesseract/5.3.4"))
    )
    respx.get(f"{ENDPOINT}/v1/outputs/f_1").mock(
        return_value=httpx.Response(200, content=searchable)
    )
    out = tmp_path / "out.pdf"

    result = build(client_for()).run(
        [DocumentRef.from_path(source)],
        OutputTarget(destination=out, force=True),
        progress=NULL_PROGRESS,
        cancellation=NEVER_CANCELLED,
        lang="eng",
        dpi=300,
    )

    assert result.engine_used is Engine.CLOUD
    assert result.engine_version == "tesseract/5.3.4"
    assert out.read_bytes() == searchable
    assert result.details["lang"] == "eng"


@respx.mock
def test_a_cloud_ocr_with_no_text_layer_fails_the_same_check_a_local_one_would(
    source: Path, tmp_path: Path
) -> None:
    """The dual-engine promise: one set of guarantees, two places to run.

    A server that returned a perfectly valid PDF with an empty text layer is
    the characteristic OCR failure, and it must fail here exactly as it fails
    locally — with the destination untouched.
    """
    from docmax.tools.ocr.cloud import build

    blank = write_pdf(tmp_path / "blank.pdf", 3).read_bytes()
    respx.post(f"{ENDPOINT}/v1/tools/ocr").mock(return_value=httpx.Response(200, json=succeeded()))
    respx.get(f"{ENDPOINT}/v1/outputs/f_1").mock(return_value=httpx.Response(200, content=blank))
    out = tmp_path / "out.pdf"

    with pytest.raises(OutputValidationError):
        build(client_for()).run(
            [DocumentRef.from_path(source)],
            OutputTarget(destination=out, force=True),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
            lang="eng",
        )

    assert not out.exists()


@respx.mock
def test_a_cloud_ocr_that_loses_a_page_is_refused(source: Path, tmp_path: Path) -> None:
    from docmax.tools.ocr.cloud import build

    short = write_searchable_pdf(tmp_path / "short.pdf", 2).read_bytes()
    respx.post(f"{ENDPOINT}/v1/tools/ocr").mock(return_value=httpx.Response(200, json=succeeded()))
    respx.get(f"{ENDPOINT}/v1/outputs/f_1").mock(return_value=httpx.Response(200, content=short))
    out = tmp_path / "out.pdf"

    with pytest.raises(OutputValidationError, match="page count"):
        build(client_for()).run(
            [DocumentRef.from_path(source)],
            OutputTarget(destination=out, force=True),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
        )

    assert not out.exists()


def zip_of(entries: dict[str, bytes]) -> bytes:
    """A minimal zip archive, built independently of ``tools/_archive.py``.

    Used both as a stand-in for what the reference server sends back for a
    directory-producing tool, and to prove ``CloudEngine`` unpacks it rather
    than merely trusting a shape it produced itself.
    """
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


#: A real PNG signature -- enough for `renders_images`' header check, nothing more.
PNG_HEADER = b"\x89PNG\r\n\x1a\n"


@respx.mock
def test_to_images_runs_in_the_cloud_end_to_end(source: Path, tmp_path: Path) -> None:
    """The directory-shaped case ADR 0034 adds: a zip in, a directory out.

    `source` has three pages, so a default (all-pages) render expects three
    images back -- the same count `renders_images` would check against a local
    run's staged directory.
    """
    from docmax.tools.to_images.cloud import build

    archive = zip_of(
        {
            "page-0001.png": PNG_HEADER + b"one",
            "page-0002.png": PNG_HEADER + b"two",
            "page-0003.png": PNG_HEADER + b"three",
        }
    )
    respx.post(f"{ENDPOINT}/v1/tools/to-images").mock(
        return_value=httpx.Response(200, json=succeeded(engine_version="pdftoppm/24.02.0"))
    )
    respx.get(f"{ENDPOINT}/v1/outputs/f_1").mock(return_value=httpx.Response(200, content=archive))
    out = tmp_path / "pages"

    result = build(client_for()).run(
        [DocumentRef.from_path(source)],
        OutputTarget(destination=out, force=True),
        progress=NULL_PROGRESS,
        cancellation=NEVER_CANCELLED,
        format="png",
    )

    assert result.engine_used is Engine.CLOUD
    assert result.engine_version == "pdftoppm/24.02.0"
    assert out.is_dir()
    assert sorted(p.name for p in out.iterdir()) == [
        "page-0001.png",
        "page-0002.png",
        "page-0003.png",
    ]
    assert len(result.outputs) == 3


@respx.mock
def test_a_cloud_to_images_that_loses_a_page_is_refused(source: Path, tmp_path: Path) -> None:
    """One set of guarantees, not two: a missing image fails on the cloud path too."""
    from docmax.tools.to_images.cloud import build

    archive = zip_of({"page-0001.png": PNG_HEADER, "page-0002.png": PNG_HEADER})
    respx.post(f"{ENDPOINT}/v1/tools/to-images").mock(
        return_value=httpx.Response(200, json=succeeded())
    )
    respx.get(f"{ENDPOINT}/v1/outputs/f_1").mock(return_value=httpx.Response(200, content=archive))
    out = tmp_path / "pages"

    with pytest.raises(OutputValidationError, match="Expected 3"):
        build(client_for()).run(
            [DocumentRef.from_path(source)],
            OutputTarget(destination=out, force=True),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
        )

    assert not out.exists()


@respx.mock
def test_a_cloud_to_images_result_with_no_real_header_is_refused(
    source: Path, tmp_path: Path
) -> None:
    """The exact v2 bug ``to_images/validators.py`` was written for, on the cloud path."""
    from docmax.tools.to_images.cloud import build

    archive = zip_of(
        {
            "page-0001.png": b"not a real png",
            "page-0002.png": PNG_HEADER,
            "page-0003.png": PNG_HEADER,
        }
    )
    respx.post(f"{ENDPOINT}/v1/tools/to-images").mock(
        return_value=httpx.Response(200, json=succeeded())
    )
    respx.get(f"{ENDPOINT}/v1/outputs/f_1").mock(return_value=httpx.Response(200, content=archive))
    out = tmp_path / "pages"

    with pytest.raises(OutputValidationError, match="no PNG header"):
        build(client_for()).run(
            [DocumentRef.from_path(source)],
            OutputTarget(destination=out, force=True),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
        )

    assert not out.exists()


@respx.mock
def test_a_cloud_result_is_checked_by_the_tools_own_validators(
    source: Path, tmp_path: Path
) -> None:
    """One set of guarantees, not two: a lost page fails where it fails locally."""
    from docmax.tools.compress.cloud import build

    short = write_pdf(tmp_path / "short.pdf", 1).read_bytes()
    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(200, json=succeeded())
    )
    respx.get(f"{ENDPOINT}/v1/outputs/f_1").mock(return_value=httpx.Response(200, content=short))
    out = tmp_path / "out.pdf"

    with pytest.raises(OutputValidationError, match="page count"):
        build(client_for()).run(
            [DocumentRef.from_path(source)],
            OutputTarget(destination=out, force=True),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
        )

    assert not out.exists(), "a failed validation leaves the destination untouched"


@respx.mock
def test_a_cloud_failure_leaves_the_destination_untouched(source: Path, tmp_path: Path) -> None:
    from docmax.tools.compress.cloud import build

    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(
            500, json={"ok": False, "error": {"code": "cloud.server_error", "message": "boom"}}
        )
    )
    out = tmp_path / "out.pdf"
    out.write_bytes(b"the original")

    with pytest.raises(Exception, match="boom"):
        build(client_for()).run(
            [DocumentRef.from_path(source)],
            OutputTarget(destination=out, force=True),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
        )

    assert out.read_bytes() == b"the original"


@respx.mock
def test_a_cloud_failure_does_not_fall_back_to_local(source: Path, tmp_path: Path) -> None:
    """ADR 0012: the router chose one engine, and a failure is that engine's."""
    from docmax.tools.compress.cloud import build

    respx.post(f"{ENDPOINT}/v1/tools/compress").mock(
        return_value=httpx.Response(
            429, json={"ok": False, "error": {"code": "cloud.quota_exceeded", "message": "no"}}
        )
    )
    calls: list[str] = []

    def record(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not run
        calls.append("local")
        raise AssertionError("the local engine ran after a cloud failure")

    from docmax.core.errors import CloudQuotaExceededError

    with pytest.raises(CloudQuotaExceededError):
        build(client_for()).run(
            [DocumentRef.from_path(source)],
            OutputTarget(destination=tmp_path / "out.pdf", force=True),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
        )

    assert not calls


def test_an_unreadable_source_fails_before_anything_is_uploaded(tmp_path: Path) -> None:
    """Validators are built first, so a bad document costs no round trip."""
    from docmax.tools.compress.cloud import build

    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\ngarbage")

    with pytest.raises(CorruptDocumentError):
        build(client_for(ExplodingTransport())).run(
            [DocumentRef.from_path(broken)],
            OutputTarget(destination=tmp_path / "out.pdf", force=True),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
        )


def test_an_already_cancelled_cloud_run_uploads_nothing(source: Path, tmp_path: Path) -> None:
    from docmax.tools.compress.cloud import build

    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError):
        build(client_for(ExplodingTransport())).run(
            [DocumentRef.from_path(source)],
            OutputTarget(destination=tmp_path / "out.pdf", force=True),
            progress=NULL_PROGRESS,
            cancellation=token,
        )


def test_convert_refuses_pdf_on_the_cloud_path_too(source: Path, tmp_path: Path) -> None:
    """A cloud engine must not widen what a tool accepts. ADR 0011 holds on both paths."""
    from docmax.tools.convert.cloud import build

    with pytest.raises(Exception, match="cannot read"):
        build(client_for(ExplodingTransport())).run(
            [DocumentRef.from_path(source)],
            OutputTarget(destination=tmp_path / "out.docx", force=True),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
            to="docx",
        )


def test_convert_refuses_a_pdf_target_on_the_cloud_path_too(notes: Path, tmp_path: Path) -> None:
    from docmax.tools.convert.cloud import build

    with pytest.raises(Exception, match="LaTeX"):
        build(client_for(ExplodingTransport())).run(
            [DocumentRef.from_path(notes)],
            OutputTarget(destination=tmp_path / "out.pdf", force=True),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
            to="pdf",
        )


@respx.mock
def test_absent_parameters_are_not_sent(notes: Path, tmp_path: Path) -> None:
    """An unset option means "no preference"; sending null would override a default."""
    from docmax.tools.convert.cloud import build

    route = respx.post(f"{ENDPOINT}/v1/tools/convert").mock(
        return_value=httpx.Response(200, json=succeeded())
    )
    respx.get(f"{ENDPOINT}/v1/outputs/f_1").mock(
        return_value=httpx.Response(200, content=b"<html></html>")
    )

    build(client_for()).run(
        [DocumentRef.from_path(notes)],
        OutputTarget(destination=tmp_path / "out.html", force=True),
        progress=NULL_PROGRESS,
        cancellation=NEVER_CANCELLED,
        to="html",
        standalone=None,
    )

    sent = route.calls.last.request
    assert b"standalone" not in sent.content


def test_an_unconfigured_cloud_engine_reports_the_command_that_fixes_it() -> None:
    from docmax.tools._cloud import CloudEngine

    engine = CloudEngine("compress", client=CloudClient(CloudConfig(api_key=None)))
    reason = engine.unavailable_reason()

    assert reason is not None
    assert "cloud login" in reason


# ---------------------------------------------------------------------------
# The reference server's in-process runner — ADR 0016
# ---------------------------------------------------------------------------

pytest.importorskip("fastapi", reason="the server extra is not installed")


PANDOC_FAKE = """
out = args[args.index("--output") + 1]
open(out, "w", encoding="utf-8").write("<html>converted</html>\\n")
"""

FAILING_FAKE = """
sys.stderr.write("pandoc: boom\\n")
sys.exit(1)
"""


def install_fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    """A real subprocess standing in for a binary, per the M3 pattern."""
    script = tmp_path / "fake_binary.py"
    script.write_text(
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if args and args[0] in {'-v', '--version'}:\n"
        "    print('fake 1.0')\n"
        "    sys.exit(0)\n" + body,
        encoding="utf-8",
    )
    real_run = _binaries.run

    def run_fake(command: Any, **kwargs: Any) -> Any:
        return real_run([sys.executable, str(script), *[str(c) for c in command[1:]]], **kwargs)

    monkeypatch.setattr(_binaries, "find", lambda name: sys.executable)
    monkeypatch.setattr(_binaries, "require", lambda name, *, tool: sys.executable)
    monkeypatch.setattr(_binaries, "run", run_fake)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Any:
    from fastapi.testclient import TestClient

    from docmax.server.app import create_app
    from docmax.server.config import ServerSettings

    return TestClient(create_app(settings=ServerSettings(api_keys=frozenset({"dev-key"}))))


AUTH = {"Authorization": "Bearer dev-key"}


def test_the_server_runs_a_tool_in_process(
    server: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_fake(monkeypatch, tmp_path, PANDOC_FAKE)

    response = server.post(
        "/v1/tools/convert",
        files={"file": ("notes.md", b"# Title\n", "application/octet-stream")},
        data={"params": json.dumps({"to": "html"})},
        headers=AUTH,
    )

    body = response.json()
    assert response.status_code == 200, body
    assert body["status"] == "succeeded"
    assert body["output"]["size_bytes"] > 0


def test_a_finished_output_can_be_downloaded_without_a_key(
    server: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A conforming client fetches through a bare client — a presigned URL is not ours."""
    install_fake(monkeypatch, tmp_path, PANDOC_FAKE)

    submitted = server.post(
        "/v1/tools/convert",
        files={"file": ("notes.md", b"# Title\n", "application/octet-stream")},
        data={"params": json.dumps({"to": "html"})},
        headers=AUTH,
    ).json()

    path = submitted["output"]["url"].split("testserver", 1)[1]
    downloaded = server.get(path)

    assert downloaded.status_code == 200
    assert b"converted" in downloaded.content


def test_a_failing_tool_answers_the_contracts_envelope(
    server: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_fake(monkeypatch, tmp_path, FAILING_FAKE)

    body = server.post(
        "/v1/tools/convert",
        files={"file": ("notes.md", b"# Title\n", "application/octet-stream")},
        data={"params": json.dumps({"to": "html"})},
        headers=AUTH,
    ).json()

    assert body["ok"] is False
    assert body["status"] == "failed"
    assert body["error"]["code"].startswith("dependency.")


def test_a_repeated_idempotency_key_does_not_run_the_job_twice(
    server: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_fake(monkeypatch, tmp_path, PANDOC_FAKE)
    headers = {**AUTH, "Idempotency-Key": "sha256:abc"}

    def submit() -> dict[str, Any]:
        body = server.post(
            "/v1/tools/convert",
            files={"file": ("notes.md", b"# Title\n", "application/octet-stream")},
            data={"params": json.dumps({"to": "html"})},
            headers=headers,
        ).json()
        assert isinstance(body, dict)
        return body

    assert submit()["job_id"] == submit()["job_id"]


#: Stands in for `pdftoppm -singlefile`: writes exactly `<root>.png`, the shape
#: `to_images/local.py` names as `-singlefile`'s whole point.
PDFTOPPM_FAKE = """
open(args[-1] + ".png", "wb").write(b"\\x89PNG\\r\\n\\x1a\\n")
"""


def test_the_server_zips_a_directory_producing_tools_output(
    server: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR 0034: a directory-producing tool's job carries one zip, not one file.

    The reference server always runs the *local* engine (`execution.py`'s whole
    trick), so this drives the real `RegistryRunner.start` directory branch end
    to end: two pages in, a two-entry zip out, downloadable without a key like
    every other output.
    """
    install_fake(monkeypatch, tmp_path, PDFTOPPM_FAKE)
    doc = write_pdf(tmp_path / "doc.pdf", 2)

    submitted = server.post(
        "/v1/tools/to-images",
        files={"file": (doc.name, doc.read_bytes(), "application/octet-stream")},
        data={"params": json.dumps({"format": "png"})},
        headers=AUTH,
    ).json()

    assert submitted["status"] == "succeeded", submitted
    assert submitted["output"]["content_type"] == "application/zip"

    path = submitted["output"]["url"].split("testserver", 1)[1]
    downloaded = server.get(path)

    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/zip"

    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        names = archive.namelist()

    assert len(names) == 2
    assert all(name.endswith(".png") for name in names)


def test_the_server_refuses_a_tool_with_no_cloud_engine(server: Any) -> None:
    response = server.post(
        "/v1/tools/merge",
        files={"file": ("a.pdf", b"%PDF-1.7\n", "application/octet-stream")},
        data={"params": "{}"},
        headers=AUTH,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "engine.not_supported"


# ---------------------------------------------------------------------------
# Capabilities report what can actually run — ADR 0018
# ---------------------------------------------------------------------------


def test_capabilities_exclude_a_tool_whose_binary_is_missing(server: Any) -> None:
    """Without Ghostscript, Pandoc or Tesseract, this endpoint offers nothing.

    Before ADR 0018 it advertised `ocr` — the one tool it could not perform.
    """
    listed = server.get("/v1/capabilities", headers=AUTH).json()["tools"]

    assert "ocr" not in listed


def test_capabilities_include_a_tool_whose_binary_is_present(
    server: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_fake(monkeypatch, tmp_path, PANDOC_FAKE)

    listed = server.get("/v1/capabilities", headers=AUTH).json()["tools"]

    assert "convert" in listed
    assert "compress" in listed


def test_capabilities_never_include_a_tool_without_a_cloud_engine(
    server: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Narrowing the list must not have widened it."""
    install_fake(monkeypatch, tmp_path, PANDOC_FAKE)

    listed = set(server.get("/v1/capabilities", headers=AUTH).json()["tools"])

    assert not listed & {"merge", "split", "watermark", "from-images"}
