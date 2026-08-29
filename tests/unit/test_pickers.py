"""The two ADR 0005 pickers.

[ADR 0005](../../docs/adr/0005-gui-pickers.md) says a picker *"needs no tests
beyond 'returns a well-formed box'"*, and that restraint is followed here: there
are no browser tests, no screenshots, and nothing that depends on a rendering
engine. What is tested is the contract the rest of the system relies on.

1. **A picker returns parameters.** The value that comes back is the value
   `--box` and `--order` already accept, parsed by the same module.
2. **A picker writes nothing.** Asserted directly, by watching the filesystem
   across a real interaction.
3. **Untrusted input stays untrusted.** The payload arrives from a browser, so
   every picker parses it into a typed value and refuses anything else.

The server tests drive a *real* loopback socket rather than a mock, because the
one thing worth proving about a short-lived HTTP server is that it starts, is
reachable, answers, and shuts down.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from docmax.core.errors import InvalidParameterError
from docmax.pickers import box as box_picker
from docmax.pickers import order as order_picker
from docmax.pickers import server as picker_server

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_pdf(path: Path, pages: int = 3, size: tuple[float, float] = (612, 792)) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=size[0], height=size[1])
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def post(url: str, payload: dict[str, Any]) -> int:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return int(response.status)


def get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return int(response.status), response.read()


def answer(payload: dict[str, Any], *, route: str = "choice") -> Callable[[str], None]:
    """An ``announce`` callback that posts ``payload`` as soon as the URL is known.

    This is how a browser is stood in for: the picker hands its URL to
    ``announce``, and instead of a person, a thread posts the value back.
    """

    def announce(url: str) -> None:
        base = url.split("  ")[0]

        def send() -> None:
            post(base + route, payload)

        threading.Thread(target=send, daemon=True).start()

    return announce


def tree(directory: Path) -> set[Path]:
    return set(directory.rglob("*"))


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------


def test_the_server_serves_the_page_and_the_document(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "in.pdf")
    fetched: dict[str, Any] = {}

    def announce(url: str) -> None:
        base = url.split("  ")[0]
        fetched["page"] = get(base)
        fetched["pdf"] = get(base + "document.pdf")
        fetched["missing"] = _status(base + "nope")
        threading.Thread(target=lambda: post(base + "choice", {"ok": 1}), daemon=True).start()

    choice = picker_server.run_picker(
        document=source,
        page="<html>hello</html>",
        announce=announce,
        open_browser=False,
    )

    assert choice.payload == {"ok": 1}
    assert fetched["page"][0] == 200
    assert b"hello" in fetched["page"][1]
    assert fetched["pdf"][1] == source.read_bytes()
    assert fetched["missing"] == 404


def _status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def test_a_wrong_token_reaches_nothing(tmp_path: Path) -> None:
    """The document is on a local port for a few seconds. Another process must
    not be able to enumerate ports and read it."""
    source = make_pdf(tmp_path / "in.pdf")
    codes: dict[str, int] = {}

    def announce(url: str) -> None:
        base = url.split("  ")[0]
        root = base.rsplit("/", 2)[0]
        codes["guessed"] = _status(f"{root}/not-the-token/document.pdf")
        threading.Thread(target=lambda: post(base + "choice", {"ok": 1}), daemon=True).start()

    picker_server.run_picker(
        document=source, page="<html></html>", announce=announce, open_browser=False
    )
    assert codes["guessed"] == 404


def test_cancelling_raises_picker_cancelled(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "in.pdf")

    with pytest.raises(picker_server.PickerCancelledError):
        picker_server.run_picker(
            document=source,
            page="<html></html>",
            announce=answer({}, route="cancel"),
            open_browser=False,
        )


def test_a_picker_that_is_never_answered_times_out(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "in.pdf")

    with pytest.raises(InvalidParameterError) as caught:
        picker_server.run_picker(
            document=source,
            page="<html></html>",
            timeout=0.5,
            open_browser=False,
        )
    assert caught.value.remedy


def test_a_picker_writes_nothing(tmp_path: Path) -> None:
    """ADR 0005: a picker never touches the filesystem. Asserted, not assumed."""
    source = make_pdf(tmp_path / "in.pdf")
    before = tree(tmp_path)

    picker_server.run_picker(
        document=source,
        page="<html></html>",
        announce=answer({"x": 0, "y": 0, "width": 10, "height": 10}),
        open_browser=False,
    )

    assert tree(tmp_path) == before
    assert source.read_bytes() == make_pdf(tmp_path / "reference.pdf").read_bytes()


def test_an_unreadable_document_is_a_typed_error(tmp_path: Path) -> None:
    with pytest.raises(InvalidParameterError):
        picker_server.run_picker(
            document=tmp_path / "absent.pdf", page="<html></html>", open_browser=False
        )


# ---------------------------------------------------------------------------
# The box picker
# ---------------------------------------------------------------------------


def test_the_box_picker_returns_a_valid_box(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "in.pdf")

    chosen = box_picker.pick_box(
        source,
        announce=answer({"x": 36, "y": 36, "width": 540, "height": 720}),
        open_browser=False,
    )

    assert chosen.as_spec() == "36,36,540,720"


def test_the_box_picker_reads_the_real_page_size(tmp_path: Path) -> None:
    """The stage is drawn to the page's true aspect ratio, so this has to be
    the document's own geometry and not an assumption about letter paper."""
    source = make_pdf(tmp_path / "a5.pdf", size=(420, 595))
    assert box_picker.page_size(source) == (420.0, 595.0)


def test_the_box_picker_refuses_a_page_that_does_not_exist(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "in.pdf", pages=2)
    with pytest.raises(InvalidParameterError) as caught:
        box_picker.page_size(source, page=9)
    assert "2" in caught.value.message


def test_a_box_off_the_page_is_refused_at_the_picker(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "in.pdf")
    with pytest.raises(InvalidParameterError):
        box_picker.pick_box(
            source,
            announce=answer({"x": 0, "y": 0, "width": 5000, "height": 5000}),
            open_browser=False,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"x": 0, "y": 0, "width": 10},
        {"x": "a", "y": 0, "width": 10, "height": 10},
        {"x": True, "y": 0, "width": 10, "height": 10},
        {"x": 0, "y": 0, "width": 0, "height": 10},
    ],
)
def test_a_malformed_box_payload_is_a_typed_error(payload: dict[str, Any], tmp_path: Path) -> None:
    """Browser input is untrusted input."""
    source = make_pdf(tmp_path / "in.pdf")
    with pytest.raises(InvalidParameterError):
        box_picker.pick_box(source, announce=answer(payload), open_browser=False)


# ---------------------------------------------------------------------------
# The order picker
# ---------------------------------------------------------------------------


def test_the_order_picker_returns_a_valid_page_order(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "in.pdf", pages=3)

    chosen = order_picker.pick_order(
        source, announce=answer({"order": [3, 1, 2]}), open_browser=False
    )

    assert chosen == "3,1,2"


def test_the_order_picker_result_is_what_reorder_accepts(tmp_path: Path) -> None:
    """The picker and the flag must produce the same value, or one of them is
    speaking a dialect."""
    from docmax.tools import _pagespec

    source = make_pdf(tmp_path / "in.pdf", pages=4)
    chosen = order_picker.pick_order(
        source, announce=answer({"order": [4, 3, 2, 1]}), open_browser=False
    )

    assert _pagespec.parse(chosen, total=4) == (3, 2, 1, 0)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"order": []},
        {"order": "3,1,2"},
        {"order": [1, 1, 3]},
        {"order": [1, 2]},
        {"order": [1, 2, "3"]},
        {"order": [1, 2, 9]},
    ],
)
def test_a_malformed_order_payload_is_a_typed_error(
    payload: dict[str, Any], tmp_path: Path
) -> None:
    source = make_pdf(tmp_path / "in.pdf", pages=3)
    with pytest.raises(InvalidParameterError):
        order_picker.pick_order(source, announce=answer(payload), open_browser=False)


# ---------------------------------------------------------------------------
# The pages themselves
# ---------------------------------------------------------------------------


def test_the_box_page_carries_the_real_geometry() -> None:
    from docmax.pickers._pages import box_page

    html = box_page(name="report.pdf", page_number=2, width=420.0, height=595.0)
    assert "420.0" in html
    assert "595.0" in html
    assert "document.pdf#page=2" in html


def test_a_filename_is_escaped_into_the_page() -> None:
    """A filename is user data on its way into markup."""
    from docmax.pickers._pages import box_page

    html = box_page(name="<script>alert(1)</script>.pdf", page_number=1, width=10, height=10)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_the_order_page_lists_every_page() -> None:
    from docmax.pickers._pages import order_page

    html = order_page(name="in.pdf", pages=4)
    for number in (1, 2, 3, 4):
        assert f'data-page="{number}"' in html


@pytest.fixture(autouse=True)
def _never_open_a_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in this file may launch a real browser, whatever it passes.

    Belt and braces: every call here already passes ``open_browser=False``, and
    a test suite that could open nineteen browser tabs on a developer's machine
    is one nobody will run twice.
    """
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda *_args, **_kwargs: False)
