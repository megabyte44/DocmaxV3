"""Straightening a scanned page before it is recognised.

A page fed to a scanner at two degrees off square costs real recognition
accuracy, and rotating it back is cheap. This is that rotation, and nothing
else.

## Why this is its own module

Because v2 got it wrong in a way that could only have been caught by a test, and
the test was impossible while the code lived inside the OCR pipeline. The
changelog records the defect precisely:

> `deskew` passed `(y, x)` points to `cv2.minAreaRect`, which expects `(x, y)`,
> and applied an angle correction written for a pre-4.5 OpenCV convention that
> never fires on current versions.

Both halves of that are avoided here deliberately and are commented where they
occur. :func:`angle_of` is a pure function from an image to a number, so
``tests/unit/test_deskew.py`` can rotate a known image by a known angle and
assert it comes back — which is the test v2 never had.

## OpenCV is imported inside the functions

``ocr/local.py`` is read on every ``--help`` through the registry walk, and
``cv2`` is a forty-megabyte import. ``tests/hygiene/test_no_heavy_imports.py``
checks this in a subprocess.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

#: Below this the rotation is not worth the resampling: every rotation is a
#: lossy interpolation, and straightening a page by a twentieth of a degree
#: costs accuracy rather than buying it.
MIN_ANGLE_DEGREES = 0.1

#: Above this the estimate is not trustworthy. ``minAreaRect`` describes the
#: bounding box of *all* dark pixels, so a page with a figure, a table or a
#: margin note can produce a box at an angle that has nothing to do with the
#: text baseline. A page genuinely forty degrees out is a scan to redo, not one
#: to rotate, and silently spinning it would be worse than leaving it alone.
MAX_ANGLE_DEGREES = 20.0

#: Rotation exposes corners that were outside the original frame. Scans are
#: dark-on-light, so they are filled white — a black wedge along one edge is
#: read by Tesseract as content and costs more than the skew did.
_WHITE = 255


def angle_of(image: Any) -> float:
    """The page's skew in degrees, positive meaning it must turn anticlockwise.

    ``0.0`` when there is nothing to measure or nothing worth correcting: a
    blank page, a page whose estimate is implausible, or one already square.

    Takes and returns plain values rather than touching the filesystem, which is
    what makes it testable without a PDF, without Tesseract, and without a
    temporary directory.
    """
    import cv2

    grey = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Otsu picks the threshold from the page's own histogram, so this works on a
    # grey scan and a pure black-and-white fax alike. Inverted, because the
    # measurement below wants the *ink* as foreground.
    _, mask = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # `findNonZero` returns points as (x, y). This is the line v2 got wrong: it
    # used `np.column_stack(np.where(...))`, which yields (row, column) — that
    # is (y, x) — and `minAreaRect` interprets them as (x, y) regardless. The
    # result was a rectangle measured across a transposed page.
    coords = cv2.findNonZero(mask)
    if coords is None or len(coords) < 2:
        return 0.0

    rect_angle = float(cv2.minAreaRect(coords)[-1])

    # Fold into (-45, 45] **without assuming which convention this OpenCV uses**.
    # That is the second half of v2's defect and it is not hypothetical: v2
    # hardcoded the pre-4.5 (-90, 0] range, 4.5 changed it to (0, 90], and
    # OpenCV 5.0 — which this was developed against — reports (-90, 0] again.
    # Any code that names a range is wrong on some install somebody has.
    #
    # The modulo is convention-independent because a rectangle is symmetric
    # under a quarter turn: -82, 8 and 98 all describe the same box, so
    # reducing modulo 90 and then folding the top half to negative yields the
    # same answer from every version.
    skew = rect_angle % 90
    if skew > 45:
        skew -= 90

    if abs(skew) < MIN_ANGLE_DEGREES or abs(skew) > MAX_ANGLE_DEGREES:
        return 0.0
    return round(float(skew), 3)


def rotate(image: Any, degrees: float) -> Any:
    """``image`` turned by ``degrees`` anticlockwise, on a white ground."""
    import cv2

    height, width = image.shape[:2]
    centre = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(centre, degrees, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(_WHITE, _WHITE, _WHITE),
    )


def straighten(path: Path) -> float:
    """Deskew the image at ``path`` in place. Returns the angle applied.

    ``0.0`` means the file was left exactly as it was — including every case
    where the estimate was untrustworthy, so a page this cannot measure is a
    page it does not touch.

    **The file is always inside a temporary directory.** v2 wrote a
    ``_preprocessed.png`` beside every source document, which folder-watch mode
    then treated as new input; ``ocr/local.py`` owns that temporary directory
    and this function only ever sees a path within it.
    """
    import cv2

    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        # Unreadable as an image. Not an error worth failing a page for — the
        # OCR step will produce a far better message about the same file.
        return 0.0

    degrees = angle_of(image)
    if degrees == 0.0:
        return 0.0

    if not cv2.imwrite(str(path), rotate(image, degrees)):
        # Written back over a file that was read a moment ago, in a directory
        # this process made. A failure here is a disk problem, and losing the
        # skew correction is a far better outcome than losing the page.
        return 0.0
    return degrees


def is_available() -> bool:
    """Is OpenCV installed? Answered without importing it."""
    import importlib.util

    return importlib.util.find_spec("cv2") is not None


__all__ = [
    "MAX_ANGLE_DEGREES",
    "MIN_ANGLE_DEGREES",
    "angle_of",
    "is_available",
    "rotate",
    "straighten",
]
