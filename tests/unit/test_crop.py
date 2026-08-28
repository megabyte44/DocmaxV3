"""``crop`` and the box vocabulary it shares with the picker.

`crop` is M7's one new tool, and it exists because
[ADR 0005](../../docs/adr/0005-gui-pickers.md) requires the headless form of a
picker to ship first: `--box` is what is tested here, and the picker is only
another way to fill it in.

The shape follows the one `merge` established and every M2 tool copied — does it
do the thing, does it refuse bad input with a *typed* error, does progress and
cancellation reach it, does a failure leave the destination untouched.

Fixtures are generated with pypdf rather than committed, so nothing here depends
on a file anyone has to keep.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from docmax.core.cancellation import NEVER_CANCELLED, CancellationToken
from docmax.core.config import Config
from docmax.core.errors import (
    CancelledError,
    InvalidParameterError,
    OutputValidationError,
)
from docmax.core.models import DocumentRef, Engine, OutputTarget
from docmax.core.protocols import NULL_PROGRESS
from docmax.core.registry import get_tool
from docmax.core.router import EngineRouter
from docmax.tools import _box

if TYPE_CHECKING:
    from docmax.core.models import ToolResult

LETTER = (612.0, 792.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_pdf(path: Path, pages: int = 3, size: tuple[float, float] = LETTER) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=size[0], height=size[1])
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def make_mixed_pdf(path: Path) -> Path:
    """Three letter pages and one that is deliberately much smaller."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=LETTER[0], height=LETTER[1])
    writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def crop(source: Path, destination: Path, box: str, **extra: Any) -> ToolResult:
    router = EngineRouter(config=Config())
    return router.run(
        "crop",
        [DocumentRef.from_path(source)],
        OutputTarget(destination=destination),
        progress=NULL_PROGRESS,
        cancellation=NEVER_CANCELLED,
        box=box,
        **extra,
    )


def sizes(path: Path) -> list[tuple[float, float]]:
    from pypdf import PdfReader

    return [
        (float(page.mediabox.width), float(page.mediabox.height))
        for page in PdfReader(str(path)).pages
    ]


# ---------------------------------------------------------------------------
# The box vocabulary
# ---------------------------------------------------------------------------


def test_a_box_is_four_numbers() -> None:
    box = _box.parse("10,10,500,700")
    assert (box.x, box.y, box.width, box.height) == (10.0, 10.0, 500.0, 700.0)
    assert (box.right, box.top) == (510.0, 710.0)


def test_a_box_round_trips_through_its_spec() -> None:
    """What a picker returns and what a user types have to be the same value."""
    original = _box.parse("10.5,0,500,700")
    assert _box.parse(original.as_spec()) == original


def test_whole_numbers_lose_their_trailing_zero() -> None:
    assert _box.parse("10,10,500,700").as_spec() == "10,10,500,700"


@pytest.mark.parametrize(
    "spec",
    ["", "   ", "1,2,3", "1,2,3,4,5", "1,2,a,4", "1,2,0,4", "1,2,4,0", "1,2,-4,4"],
)
def test_a_malformed_box_is_a_typed_error(spec: str) -> None:
    """Eagerly, and by name. v2 called bare int() on user input."""
    with pytest.raises(InvalidParameterError) as caught:
        _box.parse(spec)
    assert caught.value.remedy


def test_a_box_off_the_page_is_refused() -> None:
    with pytest.raises(InvalidParameterError) as caught:
        _box.require_within(_box.parse("0,0,900,900"), width=612, height=792)
    assert "612" in caught.value.message or "612" in (caught.value.remedy or "")


def test_a_box_exactly_the_page_is_allowed() -> None:
    _box.require_within(_box.parse("0,0,612,792"), width=612, height=792)


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


def test_crop_is_registered_with_a_required_box() -> None:
    spec = get_tool("crop")
    assert spec.supported_engines == frozenset({Engine.LOCAL})
    (param,) = spec.params
    assert param.name == "box"
    assert param.required


def test_crop_trims_every_page(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"

    result = crop(source, out, "36,36,540,720")

    assert result.outputs == (out,)
    assert result.engine_used is Engine.LOCAL
    assert sizes(out) == [(540.0, 720.0)] * 3
    assert result.details["cropped"] == 3
    assert result.details["skipped"] == []


def test_crop_sets_the_crop_box_as_well_as_the_media_box(tmp_path: Path) -> None:
    """A viewer prefers /CropBox. Writing only /MediaBox looks cropped in some
    readers and uncropped in others."""
    from pypdf import PdfReader

    source = make_pdf(tmp_path / "in.pdf", pages=1)
    out = tmp_path / "out.pdf"
    crop(source, out, "36,36,540,720")

    page = PdfReader(str(out)).pages[0]
    assert float(page.cropbox.width) == 540.0
    assert float(page.mediabox.width) == 540.0


def test_crop_leaves_pages_the_box_does_not_fit_alone(tmp_path: Path) -> None:
    """A mixed-size document is legal, and refusing all four pages because one
    is small would make the tool useless on exactly the scans that need it."""
    source = make_mixed_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"

    result = crop(source, out, "0,0,400,400")

    assert result.details["skipped"] == [4]
    assert result.details["cropped"] == 3
    assert sizes(out) == [(400.0, 400.0)] * 3 + [(200.0, 200.0)]


def test_a_box_that_fits_no_page_is_refused(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "in.pdf", pages=1, size=(200, 200))
    with pytest.raises(InvalidParameterError):
        crop(source, tmp_path / "out.pdf", "0,0,400,400")


def test_a_missing_box_is_a_typed_error(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "in.pdf")
    with pytest.raises(InvalidParameterError):
        crop(source, tmp_path / "out.pdf", "")


def test_a_bad_box_is_refused_before_the_destination_is_touched(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    with pytest.raises(InvalidParameterError):
        crop(source, out, "nonsense")
    assert not out.exists()


def test_cancellation_leaves_the_destination_untouched(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "in.pdf", pages=5)
    out = tmp_path / "out.pdf"
    token = CancellationToken()
    token.cancel()

    router = EngineRouter(config=Config())
    with pytest.raises(CancelledError):
        router.run(
            "crop",
            [DocumentRef.from_path(source)],
            OutputTarget(destination=out),
            progress=NULL_PROGRESS,
            cancellation=token,
            box="36,36,540,720",
        )
    assert not out.exists()


def test_progress_reaches_the_strategy(tmp_path: Path) -> None:
    source = make_pdf(tmp_path / "in.pdf", pages=4)

    class Recorder:
        def __init__(self) -> None:
            self.total: int | None = None
            self.steps = 0

        def start(self, description: str, *, total: int | None = None) -> None:
            self.total = total

        def advance(self, amount: int = 1) -> None:
            self.steps += amount

        def finish(self) -> None:
            pass

    recorder = Recorder()
    router = EngineRouter(config=Config())
    router.run(
        "crop",
        [DocumentRef.from_path(source)],
        OutputTarget(destination=tmp_path / "out.pdf"),
        progress=recorder,
        cancellation=NEVER_CANCELLED,
        box="36,36,540,720",
    )
    assert recorder.total == 4
    assert recorder.steps == 4


# ---------------------------------------------------------------------------
# The validator
# ---------------------------------------------------------------------------


def test_the_validator_catches_a_page_that_was_not_cropped(tmp_path: Path) -> None:
    """The failure specific to this tool: a file that opens fine and is simply
    not cropped."""
    from docmax.tools.crop.validators import cropped_to

    uncropped = make_pdf(tmp_path / "uncropped.pdf", pages=1)
    validate = cropped_to(_box.parse("0,0,100,100"), expected_pages=1)

    with pytest.raises(OutputValidationError):
        validate(uncropped)


def test_the_validator_exempts_the_pages_the_strategy_skipped(tmp_path: Path) -> None:
    from docmax.tools.crop.validators import cropped_to

    uncropped = make_pdf(tmp_path / "uncropped.pdf", pages=1)
    validate = cropped_to(_box.parse("0,0,100,100"), expected_pages=1, skipped=frozenset({1}))

    validate(uncropped)


def test_the_validator_catches_a_lost_page(tmp_path: Path) -> None:
    from docmax.tools.crop.validators import cropped_to

    produced = make_pdf(tmp_path / "two.pdf", pages=2)
    validate = cropped_to(_box.parse("0,0,612,792"), expected_pages=3, skipped=frozenset({1, 2}))

    with pytest.raises(OutputValidationError):
        validate(produced)
