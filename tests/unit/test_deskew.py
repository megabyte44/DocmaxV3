"""Straightening a scanned page — and the v2 defect that makes it worth testing.

The changelog records exactly what went wrong the first time:

> `deskew` passed `(y, x)` points to `cv2.minAreaRect`, which expects `(x, y)`,
> and applied an angle correction written for a pre-4.5 OpenCV convention that
> never fires on current versions.

Neither half could have been caught by any test that existed, because the code
lived inside the OCR pipeline and could only be reached through Tesseract. It is
a pure function now, so this file rotates a page by a known angle and asserts it
comes back.

**The convention half is not hypothetical.** OpenCV moved `minAreaRect`'s range
from `(-90, 0]` to `(0, 90]` at 4.5, and OpenCV 5.0 — which this was developed
against — reports `(-90, 0]` again. Any code that names a range is wrong on some
install somebody has, so `angle_of` names none, and
`test_the_estimate_does_not_depend_on_the_opencv_convention` pins that.

Needs OpenCV, which is the `ocr` extra. Skipped without it.
"""

from __future__ import annotations

from typing import Any

import pytest

from docmax.tools import _deskew

cv2 = pytest.importorskip("cv2", reason="deskew needs OpenCV, from the `ocr` extra")
np = pytest.importorskip("numpy")

#: How close the recovered angle must be to the one applied. Rotation resamples,
#: and the bounding box of resampled ink is not perfectly stable — a third of a
#: degree is far tighter than anything that affects recognition.
TOLERANCE = 0.35


def page(angle: float = 0.0, *, lines: int = 10) -> Any:
    """A white page with black text-like bars, rotated ``angle`` anticlockwise."""
    image = np.full((400, 600), 255, dtype=np.uint8)
    for index in range(lines):
        top = 60 + index * 28
        cv2.rectangle(image, (80, top), (520, top + 12), 0, -1)
    if angle:
        matrix = cv2.getRotationMatrix2D((300, 200), angle, 1.0)
        image = cv2.warpAffine(
            image,
            matrix,
            (600, 400),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )
    return image


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("applied", [-12.0, -8.0, -5.0, -2.0, -1.0, 1.0, 2.0, 5.0, 8.0, 12.0])
def test_the_skew_of_a_known_rotation_is_recovered(applied: float) -> None:
    """The test v2 never had.

    Both signs, deliberately: v2's transposed points made the estimate wrong in
    a way that a single-direction test would have passed.
    """
    estimate = _deskew.angle_of(page(applied))

    assert estimate == pytest.approx(-applied, abs=TOLERANCE)


@pytest.mark.parametrize("applied", [-8.0, -3.0, 3.0, 8.0])
def test_applying_the_estimate_squares_the_page(applied: float) -> None:
    """The property that actually matters: correcting twice changes nothing."""
    skewed = page(applied)

    corrected = _deskew.rotate(skewed, _deskew.angle_of(skewed))

    assert _deskew.angle_of(corrected) == pytest.approx(0.0, abs=TOLERANCE)


def test_a_square_page_is_left_alone() -> None:
    assert _deskew.angle_of(page(0.0)) == 0.0


def test_a_negligible_skew_is_not_worth_resampling() -> None:
    """Every rotation is a lossy interpolation."""
    assert _deskew.angle_of(page(0.02)) == 0.0


def test_an_implausible_estimate_is_refused() -> None:
    """A page 35 degrees out is a scan to redo, not one to silently spin.

    `minAreaRect` describes the box around *all* dark pixels, so a figure or a
    margin note can produce an angle that has nothing to do with the baseline.
    """
    assert _deskew.MAX_ANGLE_DEGREES < 35
    assert _deskew.angle_of(page(35.0)) == 0.0


def test_a_blank_page_has_no_skew() -> None:
    assert _deskew.angle_of(np.full((120, 120), 255, dtype=np.uint8)) == 0.0


def test_a_colour_page_is_handled() -> None:
    """Poppler writes RGB PNGs, so the grey conversion has to happen here."""
    colour = cv2.cvtColor(page(5.0), cv2.COLOR_GRAY2BGR)

    assert _deskew.angle_of(colour) == pytest.approx(-5.0, abs=TOLERANCE)


def test_the_estimate_does_not_depend_on_the_opencv_convention() -> None:
    """The second half of v2's defect, pinned directly.

    Whatever range this OpenCV reports, a page rotated one way and the same page
    rotated the other must produce estimates that are equal and opposite. A
    hardcoded convention breaks exactly this symmetry — it works in one
    direction and returns nonsense in the other.
    """
    left = _deskew.angle_of(page(-6.0))
    right = _deskew.angle_of(page(6.0))

    assert left == pytest.approx(-right, abs=TOLERANCE)
    assert left > 0 > right


# ---------------------------------------------------------------------------
# The file-level entry point
# ---------------------------------------------------------------------------


def test_straighten_rewrites_the_image_in_place(tmp_path: Any) -> None:
    path = tmp_path / "page.png"
    cv2.imwrite(str(path), page(6.0))
    before = path.read_bytes()

    applied = _deskew.straighten(path)

    assert applied == pytest.approx(-6.0, abs=TOLERANCE)
    assert path.read_bytes() != before
    assert _deskew.angle_of(cv2.imread(str(path), cv2.IMREAD_UNCHANGED)) == pytest.approx(
        0.0, abs=TOLERANCE
    )


def test_straighten_leaves_a_square_page_byte_identical(tmp_path: Any) -> None:
    """Nothing to correct means nothing to rewrite, and no resampling loss."""
    path = tmp_path / "page.png"
    cv2.imwrite(str(path), page(0.0))
    before = path.read_bytes()

    assert _deskew.straighten(path) == 0.0
    assert path.read_bytes() == before


def test_straighten_writes_only_the_file_it_was_given(tmp_path: Any) -> None:
    """The guarantee that makes this module's hygiene exemption safe.

    `tests/hygiene/test_no_direct_writes.py` exempts `_deskew.py` because its
    `cv2.imwrite` targets a scratch file inside a directory `ocr/local.py` owns
    — not a destination. An AST scan cannot check that. This can: the whole tree
    is compared before and after, and exactly one file may differ.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    neighbour = scratch / "other-page.png"
    cv2.imwrite(str(neighbour), page(0.0))
    target = scratch / "page.png"
    cv2.imwrite(str(target), page(7.0))

    before = {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()}

    _deskew.straighten(target)

    after = {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    assert set(after) == set(before), "no file was created or removed"
    changed = {p for p in after if after[p] != before[p]}
    assert changed == {target}
    assert neighbour.read_bytes() == before[neighbour]


def test_straighten_survives_a_file_that_is_not_an_image(tmp_path: Any) -> None:
    """A page this cannot measure is a page it does not touch."""
    path = tmp_path / "not-an-image.png"
    path.write_bytes(b"certainly not a PNG")

    assert _deskew.straighten(path) == 0.0
    assert path.read_bytes() == b"certainly not a PNG"


def test_rotation_fills_the_corners_white(tmp_path: Any) -> None:
    """A black wedge along an edge reads to Tesseract as content."""
    rotated = _deskew.rotate(page(0.0), 10.0)

    assert int(rotated[2, 2]) > 200, "the exposed corner is light, not black"


def test_availability_is_answered_without_importing_opencv() -> None:
    assert _deskew.is_available() is True
