"""The local engine for ``watermark``.

## How the text gets onto the page

An overlay page is built in memory holding nothing but a text-drawing content
stream, and pypdf merges it over each selected page. No rasterisation, so the
result stays vector text at any zoom, the file grows by a few hundred bytes
rather than by a bitmap per page, and the original content is untouched
underneath.

The font is **Helvetica**, one of the fourteen faces every conforming PDF reader
is required to have built in. That means no font file is embedded and none has
to be found on the machine -- at the cost that the text must be encodable in
Latin-1, which is checked and refused rather than mangled.

## What this is not

A watermark drawn over content can be removed by anyone with a PDF editor: it
is a separate layer, not a change to the page beneath it. This tool marks
documents; it does not protect them. Anything that must not be read is a job for
``redact``, and anything that must not be opened is a job for ``protect``.

pypdf is imported inside the methods, not at module scope.
"""

from __future__ import annotations

import importlib.util
import math
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _pagespec, _position
from docmax.tools._pdf import open_pdf, page_count, save
from docmax.tools.watermark.validators import page_count_is

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pypdf import PageObject

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, ProgressSink

DEPENDENCY = "pypdf"

#: One of the fourteen standard faces, so nothing is embedded and nothing has to
#: be installed. See the module docstring for what that costs.
FONT = "Helvetica"

#: The encoding ``FONT`` can represent. Text outside it is refused rather than
#: silently drawn as the wrong glyphs.
_TEXT_ENCODING = "latin-1"

#: Rough mean glyph width of Helvetica, as a fraction of the font size. Used
#: **only** to centre the text in its cell -- a placement that is a point or two
#: out is invisible, and the alternative is parsing font metrics to position a
#: watermark.
_MEAN_GLYPH_WIDTH = 0.5

#: How far below the visual middle of a line of text the baseline sits, again as
#: a fraction of the font size. Puts the *ink* in the middle of the cell rather
#: than the baseline.
_BASELINE_OFFSET = 0.35

#: The grey the text is drawn in, as one value for all three channels. Colour is
#: not exposed: a watermark is meant to sit behind the reader's attention, and
#: the knob that actually controls that is ``opacity``.
_GREY = 0.5


class WatermarkLocal:
    """Draw text over the selected pages with pypdf."""

    def is_available(self) -> bool:
        return importlib.util.find_spec(DEPENDENCY) is not None

    def unavailable_reason(self) -> str | None:
        if self.is_available():
            return None
        return f"{DEPENDENCY} is not installed."

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Draw ``text`` over the selected pages of ``docs[0]`` into ``target``.

        Every page is copied to the output whether or not it was selected, so a
        ``--pages`` selection marks some pages rather than discarding the rest.
        """
        import time

        from pypdf import PdfWriter

        if not docs:
            raise InvalidParameterError(
                "Watermark needs a document.",
                remedy="Pass the PDF to mark.",
            )

        text = _text(params)
        position = _position.canonical(params.get("position") or "center")
        size = _positive(params, "size", 48.0)
        opacity = _opacity(params)
        angle = _angle(params)

        started = time.monotonic()
        reader = open_pdf(docs[0])
        total = page_count(reader)
        selected = set(_pagespec.parse(params.get("pages"), total=total))

        writer = PdfWriter()
        progress.start(f"Marking {len(selected)} of {total} page(s)", total=total)
        for index in range(total):
            cancellation.raise_if_cancelled(operation="watermark")
            # Added to the writer *before* it is merged into. pypdf deprecated
            # the other order in 6.x and calls it unreliable: a page that is not
            # yet attached has no writer to register the overlay's resources
            # with, so the merged content stream can reference names that were
            # never written.
            page = writer.add_page(reader.pages[index])
            if index in selected:
                # The overlay is rebuilt per page rather than once: a document
                # may mix page sizes and orientations, and a single overlay
                # sized to page one would drift off the edge of the rest.
                page.merge_page(
                    _overlay(
                        float(page.mediabox.width),
                        float(page.mediabox.height),
                        text=text,
                        position=position,
                        size=size,
                        opacity=opacity,
                        angle=angle,
                    ),
                    over=True,
                )
            progress.advance()

        save(writer, target, validators=(page_count_is(total),))

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "pages": total,
                "marked": len(selected),
                "text": text,
                "position": position,
                "size": size,
                "opacity": opacity,
                "angle": angle,
            },
        )


# ---------------------------------------------------------------------------
# The overlay
# ---------------------------------------------------------------------------


def _overlay(
    page_width: float,
    page_height: float,
    *,
    text: str,
    position: str,
    size: float,
    opacity: float,
    angle: float,
) -> PageObject:
    """A page of the same size carrying nothing but the watermark text.

    Built with pypdf's own object types rather than by writing PDF syntax into a
    string, so the stream and its resource dictionary are constructed by the
    library that will serialise them.
    """
    from pypdf import PageObject
    from pypdf.generic import DecodedStreamObject, DictionaryObject, FloatObject, NameObject

    radians = math.radians(angle)
    cosine, sine = math.cos(radians), math.sin(radians)

    # The unrotated ink, then the axis-aligned box that ink occupies once it is
    # turned. Placing the *rotated* box is what keeps 45-degree text inside the
    # page instead of hanging off two corners of it.
    ink_width = _MEAN_GLYPH_WIDTH * size * len(text)
    ink_height = size
    box_width = abs(ink_width * cosine) + abs(ink_height * sine)
    box_height = abs(ink_width * sine) + abs(ink_height * cosine)

    x, y = _position.place(
        position,
        page_width=page_width,
        page_height=page_height,
        content_width=box_width,
        content_height=box_height,
    )
    centre_x = x + box_width / 2
    centre_y = y + box_height / 2

    # Translate to the centre of the cell, turn about that point, then draw the
    # text centred on the new origin. In that order, the rotation can never move
    # the watermark out of the cell it was placed in.
    operators = "\n".join(
        (
            "q",
            "/GS0 gs",
            f"{_GREY} {_GREY} {_GREY} rg",
            f"1 0 0 1 {_num(centre_x)} {_num(centre_y)} cm",
            f"{_num(cosine)} {_num(sine)} {_num(-sine)} {_num(cosine)} 0 0 cm",
            "BT",
            f"/F1 {_num(size)} Tf",
            f"{_num(-ink_width / 2)} {_num(-size * _BASELINE_OFFSET)} Td",
            f"({_escape(text)}) Tj",
            "ET",
            "Q",
            "",
        )
    )

    stream = DecodedStreamObject()
    stream.set_data(operators.encode(_TEXT_ENCODING))

    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject(f"/{FONT}")
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = font

    # `ca` is fill opacity and `CA` stroke opacity. Both are set because a
    # reader that honours only one must not show a solid watermark.
    graphics_state = DictionaryObject()
    graphics_state[NameObject("/Type")] = NameObject("/ExtGState")
    graphics_state[NameObject("/ca")] = FloatObject(opacity)
    graphics_state[NameObject("/CA")] = FloatObject(opacity)
    states = DictionaryObject()
    states[NameObject("/GS0")] = graphics_state

    resources = DictionaryObject()
    resources[NameObject("/Font")] = fonts
    resources[NameObject("/ExtGState")] = states

    overlay = PageObject.create_blank_page(width=page_width, height=page_height)
    overlay[NameObject("/Contents")] = stream
    overlay[NameObject("/Resources")] = resources
    return overlay


def _num(value: float) -> str:
    """A number in the form a PDF content stream will accept.

    Fixed-point, with any exponent gone: ``6.1e-17`` is a perfectly good float
    and is not a number a content stream may contain, and ``cos(90 degrees)``
    produces exactly that.
    """
    return f"{value:.4f}"


def _escape(text: str) -> str:
    """Escape the three characters that would end a PDF literal string early."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def _text(params: dict[str, Any]) -> str:
    """The words to draw, proven drawable before the document is opened."""
    value = params.get("text")
    if not isinstance(value, str) or not value.strip():
        raise InvalidParameterError(
            "Watermark needs some text to draw.",
            remedy='Pass it with --text "DRAFT".',
            context={"parameter": "text"},
        )
    try:
        value.encode(_TEXT_ENCODING)
    except UnicodeEncodeError as exc:
        # Refused rather than mangled: the standard fonts cannot represent this
        # character, and drawing a different one would be a silent wrong answer.
        raise InvalidParameterError(
            f"{FONT} cannot draw {value[exc.start : exc.end]!r}.",
            remedy="Use characters from the Latin-1 range.",
            context={"parameter": "text"},
        ) from exc
    return value


def _positive(params: dict[str, Any], name: str, default: float) -> float:
    """A number greater than zero, or a typed error naming the parameter."""
    value = params.get(name, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidParameterError(
            f"{name} must be a number, not {value!r}.",
            remedy=f"Try --{name} {default:g}.",
            context={"parameter": name},
        )
    if value <= 0:
        raise InvalidParameterError(
            f"{name} must be greater than zero, not {value}.",
            remedy=f"Try --{name} {default:g}.",
            context={"parameter": name},
        )
    return float(value)


def _opacity(params: dict[str, Any]) -> float:
    """Opacity, greater than 0 and at most 1.

    Zero is excluded deliberately. It is representable, and it produces a
    document that is larger than the original and looks identical -- a request
    that cannot have been meant, so it is refused rather than obeyed.
    """
    value = _positive(params, "opacity", 0.15)
    if value > 1:
        raise InvalidParameterError(
            f"opacity must be between 0 and 1, not {value}.",
            remedy="Use 0.15 for a faint mark, or 1 for a solid one.",
            context={"parameter": "opacity"},
        )
    return value


def _angle(params: dict[str, Any]) -> float:
    """Any angle, normalised to 0-359.

    Unlike page rotation, this is *drawn* rather than stored as a quarter turn,
    so there is nothing special about multiples of 90 and 45 is a usable default.
    """
    value = params.get("angle", 45.0)
    if value is None:
        return 45.0
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidParameterError(
            f"angle must be a number of degrees, not {value!r}.",
            remedy="Try --angle 45.",
            context={"parameter": "angle"},
        )
    return float(value) % 360


def _version() -> str:
    from importlib.metadata import version

    return f"{DEPENDENCY}/{version(DEPENDENCY)}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return WatermarkLocal()


__all__ = ["FONT", "WatermarkLocal", "build"]
