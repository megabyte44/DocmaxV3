"""The five M4 tools, tested against the contract they all share.

The shape is the one `merge` established and `test_m2_tools.py` generalised:
does it do the thing, does it refuse bad input with a *typed* error, do progress
and cancellation reach it, and does a failure leave the destination untouched.

Two things are genuinely new at M4 and get their own attention:

* **`watermark` and `stamp` add content rather than rearranging it.** Everything
  before them moved pages around, so a page count told you almost everything. It
  no longer does, and the tests assert on what actually landed on the page --
  extracted text for `watermark`, page geometry for `stamp`.
* **`protect`, `unlock` and `permissions` are the first tools that meet an
  encrypted document deliberately.** Every earlier tool refuses one. So the
  interesting cases are the ones the others never reach: a wrong password, an
  owner password versus a user password, and a permission bit surviving a round
  trip.

Fixtures are generated with pypdf rather than committed, so nothing here depends
on a file anyone has to keep.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from docmax.core.cancellation import NEVER_CANCELLED, CancellationToken
from docmax.core.config import Config
from docmax.core.errors import (
    CancelledError,
    CorruptDocumentError,
    EncryptedDocumentError,
    InvalidParameterError,
    UnsupportedFormatError,
)
from docmax.core.models import DocumentRef, Engine, OutputTarget
from docmax.core.protocols import NULL_PROGRESS
from docmax.core.registry import get_tool
from docmax.core.router import EngineRouter
from docmax.tools import _permissions, _position

if TYPE_CHECKING:
    from docmax.core.models import ToolResult

M4 = ("watermark", "stamp", "protect", "unlock", "permissions")

#: An algorithm pypdf implements in pure Python, so the always-run tests do not
#: depend on `cryptography` being installed. The AES default gets its own tests,
#: skipped when the package is absent.
RC4 = "RC4-128"

#: A marker rather than a module-level `importorskip`, which would skip every
#: test declared after it rather than the two that need the package.
needs_crypto = pytest.mark.skipif(
    importlib.util.find_spec("cryptography") is None,
    reason="the crypto extra is not installed",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_pdf(path: Path, pages: int = 3, width: float = 300, height: float = 400) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=height)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def write_encrypted_pdf(
    path: Path,
    *,
    user: str = "open-me",
    owner: str = "own-me",
    pages: int = 2,
    allow: tuple[str, ...] = _permissions.NAMES,
) -> Path:
    """A locked PDF, built with RC4 so no optional package is needed to make one."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=400)
    writer.encrypt(
        user,
        owner,
        algorithm=RC4,
        permissions_flag=_permissions.flags_for(allow),  # type: ignore[arg-type]
    )
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def page_count(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def text_on(path: Path, index: int = 0) -> str:
    from pypdf import PdfReader

    return PdfReader(str(path)).pages[index].extract_text()


def is_encrypted(path: Path) -> bool:
    from pypdf import PdfReader

    return bool(PdfReader(str(path)).is_encrypted)


class RecordingProgress:
    def __init__(self) -> None:
        self.events: list[str] = []

    def start(self, description: str, *, total: int | None = None) -> None:
        self.events.append("start")

    def advance(self, amount: int = 1) -> None:
        self.events.append("advance")

    def finish(self) -> None:
        self.events.append("finish")


@pytest.fixture
def router() -> EngineRouter:
    """The real registry and router; only the config is kept off the real disk."""
    return EngineRouter(config=Config())


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return write_pdf(tmp_path / "doc.pdf", 3)


@pytest.fixture
def stamp_file(tmp_path: Path) -> Path:
    return write_pdf(tmp_path / "stamp.pdf", 1, width=100, height=50)


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


def defaults(tool: str, stamp: Path, password: str = "open-me") -> dict[str, Any]:
    """The minimum each tool needs, so the uniform tests can call all five."""
    return {
        "watermark": {"text": "DRAFT"},
        "stamp": {},
        "protect": {"password": password, "algorithm": RC4},
        "unlock": {"password": password},
        "permissions": {"password": password},
    }[tool]


def inputs_for(tool: str, source: Path, stamp: Path) -> list[Path]:
    """`stamp` takes its overlay as a second input; everything else takes one."""
    return [source, stamp] if tool == "stamp" else [source]


# ---------------------------------------------------------------------------
# Registry and router -- every tool, uniformly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", M4)
def test_every_m4_tool_is_discoverable(name: str) -> None:
    spec = get_tool(name)

    assert spec.name == name
    assert spec.supports(Engine.LOCAL)


@pytest.mark.parametrize("name", M4)
def test_no_m4_tool_has_a_cloud_engine(name: str) -> None:
    """Pure pypdf: uploading for a millisecond-long local operation is strictly worse."""
    assert not get_tool(name).supports(Engine.CLOUD)


@pytest.mark.parametrize("name", M4)
def test_every_m4_tool_loads_its_strategy(name: str) -> None:
    strategy = get_tool(name).load_strategy(Engine.LOCAL)

    assert strategy.is_available() is True
    assert strategy.unavailable_reason() is None


@pytest.mark.parametrize("name", M4)
def test_every_m4_tool_declares_its_parameters(name: str) -> None:
    """The registry is what a TUI, an API server and an MCP schema all read."""
    spec = get_tool(name)

    assert spec.params, f"{name} declares no parameters"
    assert len({param.name for param in spec.params}) == len(spec.params)


@pytest.mark.parametrize("name", M4)
def test_every_m4_tool_refuses_a_non_pdf(
    router: EngineRouter, name: str, tmp_path: Path, stamp_file: Path
) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(UnsupportedFormatError):
        run(
            router,
            name,
            inputs_for(name, notes, stamp_file),
            tmp_path / "out.pdf",
            **defaults(name, stamp_file),
        )


@pytest.mark.parametrize("name", M4)
def test_every_m4_tool_refuses_a_corrupt_pdf(
    router: EngineRouter, name: str, tmp_path: Path, stamp_file: Path
) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\ngarbage")

    with pytest.raises(CorruptDocumentError):
        run(
            router,
            name,
            inputs_for(name, broken, stamp_file),
            tmp_path / "out.pdf",
            **defaults(name, stamp_file),
        )


@pytest.mark.parametrize("name", M4)
def test_an_already_cancelled_run_produces_nothing(
    router: EngineRouter, name: str, source: Path, tmp_path: Path, stamp_file: Path
) -> None:
    out = tmp_path / "out.pdf"
    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError):
        run(
            router,
            name,
            inputs_for(name, source, stamp_file),
            out,
            cancellation=token,
            **defaults(name, stamp_file),
        )

    assert not out.exists()


@pytest.mark.parametrize("name", ["watermark", "stamp", "protect"])
def test_the_page_tools_refuse_an_encrypted_pdf(
    router: EngineRouter, name: str, tmp_path: Path, stamp_file: Path
) -> None:
    """A locked document must be unlocked first; these three will not guess a password."""
    locked = write_encrypted_pdf(tmp_path / "locked.pdf")

    with pytest.raises(EncryptedDocumentError):
        run(
            router,
            name,
            inputs_for(name, locked, stamp_file),
            tmp_path / "out.pdf",
            **defaults(name, stamp_file),
        )


# ---------------------------------------------------------------------------
# watermark
# ---------------------------------------------------------------------------


def test_watermark_draws_the_text_on_every_page(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "marked.pdf"

    result = run(router, "watermark", source, out, text="CONFIDENTIAL")

    assert page_count(out) == 3
    assert result.details["marked"] == 3
    assert all("CONFIDENTIAL" in text_on(out, i) for i in range(3))


def test_watermark_keeps_unselected_pages_unmarked(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """A selection marks some pages. It does not discard the others."""
    out = tmp_path / "marked.pdf"

    result = run(router, "watermark", source, out, text="DRAFT", pages="1-2")

    assert page_count(out) == 3, "unselected pages are kept"
    assert result.details["marked"] == 2
    assert "DRAFT" in text_on(out, 0)
    assert "DRAFT" in text_on(out, 1)
    assert "DRAFT" not in text_on(out, 2)


def test_watermark_reports_a_position_it_would_accept_back(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """`details` travels into --json, so every value in it must be re-typable."""
    result = run(router, "watermark", source, tmp_path / "out.pdf", text="X", position="center")

    assert result.details["position"] in _position.NAMES


@pytest.mark.parametrize("position", _position.NAMES)
def test_watermark_accepts_every_named_position(
    router: EngineRouter, source: Path, tmp_path: Path, position: str
) -> None:
    out = tmp_path / f"{position}.pdf"

    result = run(router, "watermark", source, out, text="MARK", position=position)

    assert result.details["position"] == position
    assert "MARK" in text_on(out)


def test_watermark_refuses_an_unknown_position(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    with pytest.raises(InvalidParameterError):
        run(router, "watermark", source, tmp_path / "out.pdf", text="X", position="middle-ish")


@pytest.mark.parametrize("text", [None, "", "   "])
def test_watermark_refuses_empty_text(
    router: EngineRouter, source: Path, tmp_path: Path, text: Any
) -> None:
    with pytest.raises(InvalidParameterError):
        run(router, "watermark", source, tmp_path / "out.pdf", text=text)


def test_watermark_refuses_text_the_standard_font_cannot_draw(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """Refused, not mangled. Drawing a different glyph is a silent wrong answer."""
    with pytest.raises(InvalidParameterError, match="cannot draw"):
        run(router, "watermark", source, tmp_path / "out.pdf", text="机密")


def test_watermark_escapes_characters_that_would_end_the_string_early(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """An unescaped ')' would close the PDF literal and corrupt the stream."""
    out = tmp_path / "out.pdf"

    run(router, "watermark", source, out, text="DRAFT (v2) \\ FINAL")

    assert "DRAFT (v2) \\ FINAL" in text_on(out)


@pytest.mark.parametrize("opacity", [0, -1, 1.5, "half"])
def test_watermark_refuses_a_nonsense_opacity(
    router: EngineRouter, source: Path, tmp_path: Path, opacity: Any
) -> None:
    with pytest.raises(InvalidParameterError):
        run(router, "watermark", source, tmp_path / "out.pdf", text="X", opacity=opacity)


@pytest.mark.parametrize("size", [0, -10, "big"])
def test_watermark_refuses_a_nonsense_size(
    router: EngineRouter, source: Path, tmp_path: Path, size: Any
) -> None:
    with pytest.raises(InvalidParameterError):
        run(router, "watermark", source, tmp_path / "out.pdf", text="X", size=size)


def test_watermark_refuses_a_nonsense_angle(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    with pytest.raises(InvalidParameterError):
        run(router, "watermark", source, tmp_path / "out.pdf", text="X", angle="sideways")


def test_watermark_writes_no_exponent_into_the_content_stream(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """`cos(90 degrees)` is 6.1e-17, and a PDF content stream may not contain that."""
    out = tmp_path / "out.pdf"

    run(router, "watermark", source, out, text="SIDEWAYS", angle=90)

    assert "SIDEWAYS" in text_on(out)
    assert b"e-1" not in out.read_bytes(), "an exponent reached the content stream"


def test_watermark_reports_progress_and_finishes(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    progress = RecordingProgress()

    run(router, "watermark", source, tmp_path / "out.pdf", text="X", progress=progress)

    assert progress.events[0] == "start"
    assert progress.events.count("advance") == 3
    assert progress.events[-1] == "finish"


def test_watermark_leaves_the_destination_untouched_when_cancelled(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out.pdf"
    out.write_bytes(b"the original")
    token = CancellationToken()

    class CancelAfterFirstPage:
        def start(self, description: str, *, total: int | None = None) -> None: ...

        def advance(self, amount: int = 1) -> None:
            token.cancel()

        def finish(self) -> None: ...

    with pytest.raises(CancelledError):
        run(
            router,
            "watermark",
            source,
            out,
            text="X",
            progress=CancelAfterFirstPage(),
            cancellation=token,
        )

    assert out.read_bytes() == b"the original"
    assert not list(tmp_path.glob(".docmax-*"))


# ---------------------------------------------------------------------------
# stamp
# ---------------------------------------------------------------------------


def test_stamp_draws_the_overlay_on_every_page(
    router: EngineRouter, source: Path, stamp_file: Path, tmp_path: Path
) -> None:
    out = tmp_path / "stamped.pdf"

    result = run(router, "stamp", [source, stamp_file], out)

    assert page_count(out) == 3, "the stamp's pages join the drawing, not the document"
    assert result.details["stamped"] == 3
    assert result.details["stamp"] == "stamp.pdf"


def test_stamp_keeps_unselected_pages_unstamped(
    router: EngineRouter, source: Path, stamp_file: Path, tmp_path: Path
) -> None:
    out = tmp_path / "stamped.pdf"

    result = run(router, "stamp", [source, stamp_file], out, pages="2")

    assert page_count(out) == 3
    assert result.details["stamped"] == 1


def test_stamp_reports_a_multi_page_stamp_rather_than_hiding_it(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """Only page one is drawn. A stamp that grew a second page should be visible."""
    overlay = write_pdf(tmp_path / "overlay.pdf", 3, width=100, height=50)

    result = run(router, "stamp", [source, overlay], tmp_path / "out.pdf")

    assert result.details["stamp_pages"] == 3


def test_stamp_needs_an_overlay(router: EngineRouter, source: Path, tmp_path: Path) -> None:
    with pytest.raises(InvalidParameterError, match="two documents"):
        run(router, "stamp", [source], tmp_path / "out.pdf")


def test_stamp_refuses_an_overlay_that_is_not_a_pdf(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    notes = tmp_path / "logo.txt"
    notes.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(UnsupportedFormatError):
        run(router, "stamp", [source, notes], tmp_path / "out.pdf")


def test_stamp_refuses_an_encrypted_overlay(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    locked = write_encrypted_pdf(tmp_path / "locked-logo.pdf")

    with pytest.raises(EncryptedDocumentError):
        run(router, "stamp", [source, locked], tmp_path / "out.pdf")


@pytest.mark.parametrize("position", _position.NAMES)
def test_stamp_accepts_every_named_position(
    router: EngineRouter, source: Path, stamp_file: Path, tmp_path: Path, position: str
) -> None:
    out = tmp_path / f"{position}.pdf"

    result = run(router, "stamp", [source, stamp_file], out, position=position)

    assert result.details["position"] == position
    assert page_count(out) == 3


def test_stamp_defaults_to_the_bottom_right_corner(
    router: EngineRouter, source: Path, stamp_file: Path, tmp_path: Path
) -> None:
    """The default ADR 0005 names when it explains why this tool takes no picker."""
    result = run(router, "stamp", [source, stamp_file], tmp_path / "out.pdf")

    assert result.details["position"] == "bottom-right"


def test_stamp_refuses_an_unknown_position(
    router: EngineRouter, source: Path, stamp_file: Path, tmp_path: Path
) -> None:
    with pytest.raises(InvalidParameterError):
        run(router, "stamp", [source, stamp_file], tmp_path / "out.pdf", position="corner")


@pytest.mark.parametrize("scale", [0, -2, "double"])
def test_stamp_refuses_a_nonsense_scale(
    router: EngineRouter, source: Path, stamp_file: Path, tmp_path: Path, scale: Any
) -> None:
    with pytest.raises(InvalidParameterError):
        run(router, "stamp", [source, stamp_file], tmp_path / "out.pdf", scale=scale)


def test_stamp_accepts_a_stamp_larger_than_the_page(
    router: EngineRouter, source: Path, stamp_file: Path, tmp_path: Path
) -> None:
    """A full-page VOID overlay is a legitimate request, not an error."""
    out = tmp_path / "out.pdf"

    result = run(router, "stamp", [source, stamp_file], out, scale=20)

    assert result.details["scale"] == 20.0
    assert page_count(out) == 3


def test_stamp_reports_progress_and_finishes(
    router: EngineRouter, source: Path, stamp_file: Path, tmp_path: Path
) -> None:
    progress = RecordingProgress()

    run(router, "stamp", [source, stamp_file], tmp_path / "out.pdf", progress=progress)

    assert progress.events[0] == "start"
    assert progress.events.count("advance") == 3
    assert progress.events[-1] == "finish"


def test_stamp_refuses_to_overwrite_its_own_overlay(source: Path, stamp_file: Path) -> None:
    """The reason the overlay is an input: `OutputTarget` guards what it is given."""
    from docmax.core.errors import InPlaceOverwriteError

    router = EngineRouter(config=Config())
    docs = [DocumentRef.from_path(source), DocumentRef.from_path(stamp_file)]

    with pytest.raises(InPlaceOverwriteError):
        router.target_for("stamp", docs, requested=str(stamp_file), force=True)


# ---------------------------------------------------------------------------
# protect
# ---------------------------------------------------------------------------


def opened_with(path: Path, password: str) -> Any:
    from pypdf import PdfReader

    return PdfReader(str(path)).decrypt(password)


def permissions_of(path: Path, password: str) -> dict[str, bool]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    reader.decrypt(password)
    return _permissions.describe(reader.user_access_permissions)


def test_protect_encrypts_the_document(router: EngineRouter, source: Path, tmp_path: Path) -> None:
    out = tmp_path / "locked.pdf"

    result = run(router, "protect", source, out, password="s3cret", algorithm=RC4)

    assert is_encrypted(out)
    assert result.details["pages"] == 3
    assert result.details["algorithm"] == RC4


def test_protect_keeps_every_page(router: EngineRouter, source: Path, tmp_path: Path) -> None:
    from pypdf import PdfReader

    out = tmp_path / "locked.pdf"

    run(router, "protect", source, out, password="s3cret", algorithm=RC4)

    reader = PdfReader(str(out))
    reader.decrypt("s3cret")
    assert len(reader.pages) == 3


def test_protect_needs_a_password(router: EngineRouter, source: Path, tmp_path: Path) -> None:
    with pytest.raises(InvalidParameterError, match="needs a password"):
        run(router, "protect", source, tmp_path / "out.pdf", algorithm=RC4)


def test_protect_accepts_an_empty_user_password(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """Open freely, but the permissions apply. A real arrangement, not an omission."""
    out = tmp_path / "locked.pdf"

    run(
        router,
        "protect",
        source,
        out,
        password="",
        owner_password="owner-only",
        allow="print",
        algorithm=RC4,
    )

    assert is_encrypted(out)
    assert permissions_of(out, "")["print"] is True
    assert permissions_of(out, "")["copy"] is False


def test_protect_uses_the_user_password_as_the_owner_password_by_default(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """Nobody ends up holding an owner password they were never shown."""
    out = tmp_path / "locked.pdf"

    result = run(router, "protect", source, out, password="same", algorithm=RC4)

    assert result.details["distinct_owner_password"] is False
    assert opened_with(out, "same")


def test_protect_records_two_distinct_passwords_without_revealing_either(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """`details` travels into logs and --json. A password must never be in it."""
    out = tmp_path / "locked.pdf"

    result = run(
        router,
        "protect",
        source,
        out,
        password="user-pw",
        owner_password="owner-pw",
        algorithm=RC4,
    )

    assert result.details["distinct_owner_password"] is True
    assert "user-pw" not in str(result.details)
    assert "owner-pw" not in str(result.details)


def test_protect_grants_everything_by_default(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """A protect that quietly forbade printing would be found much later, at a printer."""
    out = tmp_path / "locked.pdf"

    result = run(router, "protect", source, out, password="pw", algorithm=RC4)

    assert set(result.details["allowed"]) == set(_permissions.NAMES)
    assert all(permissions_of(out, "pw").values())


@pytest.mark.parametrize("name", _permissions.NAMES)
def test_protect_can_grant_exactly_one_permission(
    router: EngineRouter, source: Path, tmp_path: Path, name: str
) -> None:
    out = tmp_path / "locked.pdf"

    run(router, "protect", source, out, password="pw", allow=name, algorithm=RC4)

    granted = permissions_of(out, "pw")
    assert granted[name] is True
    assert not any(value for key, value in granted.items() if key != name)


def test_protect_accepts_a_comma_separated_allow_list(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "locked.pdf"

    result = run(router, "protect", source, out, password="pw", allow="print,copy", algorithm=RC4)

    assert set(result.details["allowed"]) == {"print", "copy"}


def test_protect_accepts_a_repeated_allow_option(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """`--allow print --allow copy` must mean the same as `--allow print,copy`."""
    out = tmp_path / "locked.pdf"

    result = run(
        router, "protect", source, out, password="pw", allow=["print", "copy"], algorithm=RC4
    )

    assert set(result.details["allowed"]) == {"print", "copy"}


def test_protect_allows_nothing_when_asked(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "locked.pdf"

    result = run(router, "protect", source, out, password="pw", allow="none", algorithm=RC4)

    assert result.details["allowed"] == []
    assert not any(permissions_of(out, "pw").values())


def test_protect_refuses_an_unknown_permission(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    with pytest.raises(InvalidParameterError, match="not a permission"):
        run(router, "protect", source, tmp_path / "out.pdf", password="pw", allow="teleport")


def test_protect_refuses_an_unknown_algorithm(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """Refused before the document is opened, not by a bare ValueError inside pypdf."""
    with pytest.raises(InvalidParameterError, match="not an encryption algorithm"):
        run(router, "protect", source, tmp_path / "out.pdf", password="pw", algorithm="ROT13")


@pytest.mark.parametrize("algorithm", ["RC4-40", "RC4-128"])
def test_protect_writes_every_rc4_algorithm_without_an_extra_package(
    router: EngineRouter, source: Path, tmp_path: Path, algorithm: str
) -> None:
    out = tmp_path / "locked.pdf"

    run(router, "protect", source, out, password="pw", algorithm=algorithm)

    assert is_encrypted(out)


def test_protect_reports_progress_and_finishes(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    progress = RecordingProgress()

    run(
        router,
        "protect",
        source,
        tmp_path / "out.pdf",
        password="pw",
        algorithm=RC4,
        progress=progress,
    )

    assert progress.events[0] == "start"
    assert progress.events.count("advance") == 3
    assert progress.events[-1] == "finish"


def test_protect_leaves_the_destination_untouched_when_cancelled(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out.pdf"
    out.write_bytes(b"the original")
    token = CancellationToken()

    class CancelAfterFirstPage:
        def start(self, description: str, *, total: int | None = None) -> None: ...

        def advance(self, amount: int = 1) -> None:
            token.cancel()

        def finish(self) -> None: ...

    with pytest.raises(CancelledError):
        run(
            router,
            "protect",
            source,
            out,
            password="pw",
            algorithm=RC4,
            progress=CancelAfterFirstPage(),
            cancellation=token,
        )

    assert out.read_bytes() == b"the original"


def test_protect_validator_rejects_an_unencrypted_staged_file(tmp_path: Path) -> None:
    """The failure this tool must never deliver: a readable PDF that is not locked."""
    from docmax.core.errors import OutputValidationError
    from docmax.tools.protect.validators import encrypts_to

    plain = write_pdf(tmp_path / "plain.pdf", 2)

    with pytest.raises(OutputValidationError, match="not encrypted"):
        encrypts_to(2, password="pw")(plain)


def test_protect_validator_rejects_a_staged_file_that_lost_pages(tmp_path: Path) -> None:
    from docmax.core.errors import OutputValidationError
    from docmax.tools.protect.validators import encrypts_to

    locked = write_encrypted_pdf(tmp_path / "locked.pdf", user="pw", owner="pw", pages=2)

    with pytest.raises(OutputValidationError, match="Expected 5 page"):
        encrypts_to(5, password="pw")(locked)


# -- AES, which needs an optional package -----------------------------------
#
# The default algorithm is AES-256, so these cover the path most users take. They
# are skipped rather than failed where the `crypto` extra is absent: the RC4
# tests above already prove the tool works without it, and CI installs `[all]`.


@needs_crypto
@pytest.mark.parametrize("algorithm", ["AES-128", "AES-256"])
def test_protect_writes_aes_when_cryptography_is_installed(
    router: EngineRouter, source: Path, tmp_path: Path, algorithm: str
) -> None:
    out = tmp_path / "locked.pdf"

    result = run(router, "protect", source, out, password="pw", algorithm=algorithm)

    assert is_encrypted(out)
    assert result.details["algorithm"] == algorithm
    assert opened_with(out, "pw")


@needs_crypto
def test_protect_defaults_to_aes_256(router: EngineRouter, source: Path, tmp_path: Path) -> None:
    """The default is the strong one. RC4 has to be asked for by name."""
    out = tmp_path / "locked.pdf"

    result = run(router, "protect", source, out, password="pw")

    assert result.details["algorithm"] == "AES-256"


# ---------------------------------------------------------------------------
# unlock
# ---------------------------------------------------------------------------


@pytest.fixture
def locked(tmp_path: Path) -> Path:
    return write_encrypted_pdf(tmp_path / "locked.pdf")


def test_unlock_removes_the_password(router: EngineRouter, locked: Path, tmp_path: Path) -> None:
    out = tmp_path / "open.pdf"

    result = run(router, "unlock", locked, out, password="open-me")

    assert not is_encrypted(out)
    assert page_count(out) == 2
    assert result.details["was_encrypted"] is True


def test_unlock_accepts_the_owner_password_too(
    router: EngineRouter, locked: Path, tmp_path: Path
) -> None:
    """Either password decrypts the content; they differ in what else they permit."""
    out = tmp_path / "open.pdf"

    result = run(router, "unlock", locked, out, password="own-me")

    assert not is_encrypted(out)
    assert result.details["opened_with"] == "owner"


def test_unlock_reports_which_password_matched(
    router: EngineRouter, locked: Path, tmp_path: Path
) -> None:
    result = run(router, "unlock", locked, tmp_path / "open.pdf", password="open-me")

    assert result.details["opened_with"] == "user"


def test_unlock_never_reports_the_password_itself(
    router: EngineRouter, locked: Path, tmp_path: Path
) -> None:
    """`details` travels into logs and --json."""
    result = run(router, "unlock", locked, tmp_path / "open.pdf", password="open-me")

    assert "open-me" not in str(result.details)


def test_unlock_refuses_a_wrong_password(
    router: EngineRouter, locked: Path, tmp_path: Path
) -> None:
    out = tmp_path / "open.pdf"

    with pytest.raises(EncryptedDocumentError, match="does not open"):
        run(router, "unlock", locked, out, password="not-it")

    assert not out.exists()


def test_unlock_asks_for_a_password_it_was_not_given(
    router: EngineRouter, locked: Path, tmp_path: Path
) -> None:
    with pytest.raises(EncryptedDocumentError, match="password-protected"):
        run(router, "unlock", locked, tmp_path / "open.pdf")


def test_unlock_copies_an_unencrypted_document_rather_than_refusing(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """Failing here would break the obvious batch over a folder of mixed files."""
    out = tmp_path / "open.pdf"

    result = run(router, "unlock", source, out, password="unused")

    assert page_count(out) == 3
    assert result.details["was_encrypted"] is False
    assert result.details["opened_with"] is None


def test_unlock_drops_the_permissions_with_the_encryption(
    router: EngineRouter, tmp_path: Path
) -> None:
    """There is nowhere left to store a permission bit once the encryption is gone."""
    from pypdf import PdfReader

    restricted = write_encrypted_pdf(tmp_path / "restricted.pdf", allow=("print",))
    out = tmp_path / "open.pdf"

    run(router, "unlock", restricted, out, password="own-me")

    assert PdfReader(str(out)).user_access_permissions is None


def test_unlock_reports_progress_and_finishes(
    router: EngineRouter, locked: Path, tmp_path: Path
) -> None:
    progress = RecordingProgress()

    run(router, "unlock", locked, tmp_path / "open.pdf", password="open-me", progress=progress)

    assert progress.events[0] == "start"
    assert progress.events.count("advance") == 2
    assert progress.events[-1] == "finish"


def test_unlock_leaves_the_destination_untouched_when_cancelled(
    router: EngineRouter, locked: Path, tmp_path: Path
) -> None:
    out = tmp_path / "open.pdf"
    out.write_bytes(b"the original")
    token = CancellationToken()

    class CancelAfterFirstPage:
        def start(self, description: str, *, total: int | None = None) -> None: ...

        def advance(self, amount: int = 1) -> None:
            token.cancel()

        def finish(self) -> None: ...

    with pytest.raises(CancelledError):
        run(
            router,
            "unlock",
            locked,
            out,
            password="open-me",
            progress=CancelAfterFirstPage(),
            cancellation=token,
        )

    assert out.read_bytes() == b"the original"


def test_unlock_validator_rejects_a_staged_file_that_is_still_encrypted(
    tmp_path: Path,
) -> None:
    """The failure this tool must never deliver: output that still needs a password."""
    from docmax.core.errors import OutputValidationError
    from docmax.tools.unlock.validators import decrypts_to

    still_locked = write_encrypted_pdf(tmp_path / "still.pdf", pages=2)

    with pytest.raises(OutputValidationError, match="still needs a password"):
        decrypts_to(2)(still_locked)


def test_unlock_validator_rejects_a_staged_file_that_lost_pages(tmp_path: Path) -> None:
    from docmax.core.errors import OutputValidationError
    from docmax.tools.unlock.validators import decrypts_to

    plain = write_pdf(tmp_path / "plain.pdf", 2)

    with pytest.raises(OutputValidationError, match="Expected 5 page"):
        decrypts_to(5)(plain)


def test_protect_then_unlock_is_a_round_trip(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """The two halves of M4's encryption story, against each other."""
    sealed = tmp_path / "sealed.pdf"
    opened = tmp_path / "opened.pdf"

    run(router, "protect", source, sealed, password="round-trip", algorithm=RC4)
    run(router, "unlock", sealed, opened, password="round-trip")

    assert is_encrypted(sealed)
    assert not is_encrypted(opened)
    assert page_count(opened) == page_count(source) == 3


# ---------------------------------------------------------------------------
# permissions
# ---------------------------------------------------------------------------


def test_permissions_writes_nothing(router: EngineRouter, source: Path, tmp_path: Path) -> None:
    """Read-only. `outputs` is empty and no file appears at the unused target."""
    unused = tmp_path / "never-written.pdf"

    result = run(router, "permissions", source, unused)

    assert result.outputs == ()
    assert not unused.exists()


def test_permissions_reports_everything_allowed_for_an_unencrypted_document(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """Not a default: a file with no encryption has no permission field to restrict it."""
    result = run(router, "permissions", source, tmp_path / "unused.pdf")

    assert result.details["encrypted"] is False
    assert result.details["opened_with"] is None
    assert result.details["permissions"] == dict.fromkeys(_permissions.NAMES, True)


def test_permissions_needs_no_password_for_an_unencrypted_document(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    result = run(router, "permissions", source, tmp_path / "unused.pdf")

    assert result.details["encrypted"] is False


def test_permissions_reports_what_protect_granted(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """The two tools against each other: what one writes, the other must read back."""
    sealed = tmp_path / "sealed.pdf"
    run(router, "protect", source, sealed, password="pw", allow="print,copy", algorithm=RC4)

    result = run(router, "permissions", sealed, tmp_path / "unused.pdf", password="pw")

    granted = result.details["permissions"]
    assert granted["print"] is True
    assert granted["copy"] is True
    assert granted["modify"] is False
    assert granted["assemble"] is False


def test_permissions_reports_which_password_opened_the_document(
    router: EngineRouter, tmp_path: Path
) -> None:
    """An owner password bypasses every bit, so which one was used changes the reading."""
    restricted = write_encrypted_pdf(tmp_path / "restricted.pdf", allow=("print",))

    as_user = run(router, "permissions", restricted, tmp_path / "unused.pdf", password="open-me")
    as_owner = run(router, "permissions", restricted, tmp_path / "unused.pdf", password="own-me")

    assert as_user.details["opened_with"] == "user"
    assert as_owner.details["opened_with"] == "owner"


def test_permissions_asks_for_a_password_it_was_not_given(
    router: EngineRouter, locked: Path, tmp_path: Path
) -> None:
    with pytest.raises(EncryptedDocumentError, match="password-protected"):
        run(router, "permissions", locked, tmp_path / "unused.pdf")


def test_permissions_refuses_a_wrong_password(
    router: EngineRouter, locked: Path, tmp_path: Path
) -> None:
    with pytest.raises(EncryptedDocumentError, match="does not open"):
        run(router, "permissions", locked, tmp_path / "unused.pdf", password="not-it")


def test_permissions_never_reports_the_password_itself(
    router: EngineRouter, locked: Path, tmp_path: Path
) -> None:
    result = run(router, "permissions", locked, tmp_path / "unused.pdf", password="open-me")

    assert "open-me" not in str(result.details)


def test_permissions_says_the_bits_are_advisory(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    """A caller reading --json is told as plainly as one reading the terminal."""
    result = run(router, "permissions", source, tmp_path / "unused.pdf")

    assert result.details["advisory"] is True


def test_permissions_describes_every_name_it_reports(
    router: EngineRouter, source: Path, tmp_path: Path
) -> None:
    result = run(router, "permissions", source, tmp_path / "unused.pdf")

    assert set(result.details["descriptions"]) == set(result.details["permissions"])


# ---------------------------------------------------------------------------
# The shared vocabularies
# ---------------------------------------------------------------------------


def test_position_places_top_above_bottom() -> None:
    """PDF user space counts upwards from the bottom-left. Getting it backwards is silent."""
    top = _position.place(
        "top-left", page_width=200, page_height=400, content_width=10, content_height=10
    )
    bottom = _position.place(
        "bottom-left", page_width=200, page_height=400, content_width=10, content_height=10
    )

    assert top[1] > bottom[1]


def test_position_centres_without_using_the_margin() -> None:
    """A margin is a distance from an edge, and a centred thing is not near one."""
    x, y = _position.place(
        "center", page_width=200, page_height=400, content_width=20, content_height=40, margin=99
    )

    assert (x, y) == (90.0, 180.0)


def test_position_insets_a_corner_by_the_margin() -> None:
    x, y = _position.place(
        "bottom-left",
        page_width=200,
        page_height=400,
        content_width=20,
        content_height=40,
        margin=10,
    )

    assert (x, y) == (10.0, 10.0)


def test_position_canonicalises_center_to_a_name_it_accepts() -> None:
    assert _position.canonical("center") == "center"
    assert _position.canonical("center") in _position.NAMES


@pytest.mark.parametrize("name", ["", None, "middle", "top", "up-left", "top-middle"])
def test_position_refuses_anything_that_is_not_a_cell(name: Any) -> None:
    with pytest.raises(InvalidParameterError):
        _position.parse(name)


def test_permission_names_round_trip_through_flags() -> None:
    """What `protect` writes and `permissions` reads must be the same vocabulary."""
    for name in _permissions.NAMES:
        described = _permissions.describe(_permissions.flags_for([name]))
        assert described[name] is True
        assert sum(described.values()) == 1


def test_permission_describe_reads_no_encryption_as_no_restriction() -> None:
    assert _permissions.describe(None) == dict.fromkeys(_permissions.NAMES, True)


def test_permission_parse_collapses_duplicates_and_keeps_one_order() -> None:
    """This describes a set. `print,print` is not a different request from `print`."""
    assert _permissions.parse("copy,print,copy") == _permissions.parse(["print", "copy"])


def test_permission_parse_accepts_all_and_none() -> None:
    assert _permissions.parse("all") == _permissions.NAMES
    assert _permissions.parse("none") == ()


def test_permission_flags_for_nothing_is_zero() -> None:
    """A document that may only be opened and read is a legitimate request."""
    assert _permissions.flags_for(()) == 0


@pytest.mark.parametrize("name", ["teleport", "PRINT-ALL", 7, None])
def test_permission_parse_refuses_an_unknown_name(name: Any) -> None:
    with pytest.raises(InvalidParameterError):
        _permissions.parse([name])
