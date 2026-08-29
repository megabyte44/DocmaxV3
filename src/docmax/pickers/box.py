"""The crop box picker: drag a rectangle on a page, get four numbers back.

ADR 0005's worked example, and the one that motivated the whole mechanism —
"where to crop" is the parameter a terminal genuinely cannot ask for.

This module opens the document **read-only**, and only to learn how big the page
is. It never writes, never imports an engine, and returns a
:class:`~docmax.tools._box.PageBox` that is indistinguishable from one the user
typed after ``--box``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.pickers._pages import box_page
from docmax.pickers.server import DEFAULT_TIMEOUT_SECONDS, run_picker
from docmax.tools import _box
from docmax.tools._pdf import open_pdf, page_count, page_geometry

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def pick_box(
    source: Path,
    *,
    page: int = 1,
    announce: Callable[[str], None] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    open_browser: bool = True,
) -> _box.PageBox:
    """Open a picker on ``page`` of ``source`` and return the box the user drew.

    Raises :class:`~docmax.pickers.server.PickerCancelledError` if they closed it
    without choosing, and :class:`InvalidParameterError` if what came back was
    not a box that fits the page — the payload is browser input, so it is
    validated exactly as a typed ``--box`` would be.
    """
    width, height = page_size(source, page=page)

    choice = run_picker(
        document=source,
        page=box_page(name=source.name, page_number=page, width=width, height=height),
        announce=announce,
        timeout=timeout,
        open_browser=open_browser,
    )

    chosen = _box_from(choice.payload)
    # The same check the tool performs, applied here so a nonsensical payload is
    # refused at the picker rather than surfacing later as a failed crop.
    _box.require_within(chosen, width=width, height=height)
    return chosen


def page_size(source: Path, *, page: int = 1) -> tuple[float, float]:
    """The width and height of one page in points, for drawing a stage to scale.

    Separate from :func:`pick_box` so it can be tested without a browser, and
    because it is the *only* thing this module learns about the document.
    """
    from docmax.core.models import DocumentRef

    reader = open_pdf(DocumentRef.from_path(source))
    total = page_count(reader)
    if page < 1 or page > total:
        raise InvalidParameterError(
            f"{source.name} has {total} page(s), so there is no page {page}.",
            remedy=f"Choose a page between 1 and {total}.",
            context={"path": str(source), "page": page, "pages": total},
        )
    return page_geometry(reader, page - 1)


def _box_from(payload: dict[str, Any]) -> _box.PageBox:
    """A :class:`PageBox` from what the browser posted, or a typed error.

    Built by handing the four values to the *same* parser ``--box`` uses, rather
    than constructing a ``PageBox`` directly. A picker that validated its own
    output would be a second implementation of the box contract, and the two
    would eventually disagree about what ``0`` width means.
    """
    try:
        values = [payload["x"], payload["y"], payload["width"], payload["height"]]
    except KeyError as exc:
        raise InvalidParameterError(
            "The picker returned an incomplete box.",
            remedy="Try again, or pass --box x,y,width,height instead.",
            context={"missing": str(exc)},
        ) from exc

    for value in values:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise InvalidParameterError(
                f"The picker returned {value!r}, which is not a number.",
                remedy="Try again, or pass --box x,y,width,height instead.",
                context={"value": str(value)},
            )

    return _box.parse(",".join(repr(float(value)) for value in values))


__all__ = ["page_size", "pick_box"]
