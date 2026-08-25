"""Where on a page something goes, named the same way by every tool that places one.

Two tools put content onto an existing page — ``watermark`` and ``stamp`` — and
they must name the same nine places identically. A user who learns
``bottom-right`` for one must not discover the other spells it differently. So
the vocabulary is parsed once, here, exactly as ``_pagespec`` owns page
selections.

[ADR 0005](../../../docs/adr/0005-gui-pickers.md) settles why these are *named*
rather than picked visually: a nine-cell grid is not a coordinate anyone needs
to see the page to choose, so neither tool qualifies for a picker.

## The grid

```
top-left       top-center       top-right
center-left    center           center-right
bottom-left    bottom-center    bottom-right
```

``center`` on its own is the middle cell; it is the one name that is not a
``vertical-horizontal`` pair, because "center-center" reads like a typo.

Private to ``tools``, like ``_pagespec`` and ``_pdf``: no package of its own, so
the registry's directory walk never sees it. Nothing here imports pypdf — the
geometry is arithmetic on numbers the caller already has.
"""

from __future__ import annotations

from docmax.core.errors import InvalidParameterError

#: Every accepted name, in reading order, which is also the order ``--help``
#: should list them in.
NAMES: tuple[str, ...] = (
    "top-left",
    "top-center",
    "top-right",
    "center-left",
    "center",
    "center-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)

#: The default gap from the page edge, in points. 36pt is half an inch, which is
#: inside the printable area of every common paper size and every printer that
#: reserves a margin of its own.
DEFAULT_MARGIN = 36.0

_VERTICAL = frozenset({"top", "center", "bottom"})
_HORIZONTAL = frozenset({"left", "center", "right"})


def parse(name: str | None) -> tuple[str, str]:
    """Split a position name into its ``(vertical, horizontal)`` halves.

    ``None`` is not defaulted here: each tool has its own sensible default —
    ``watermark`` sits in the middle, ``stamp`` in a corner — and a shared
    default would have to be wrong for one of them.
    """
    text = (name or "").strip().lower()
    if not text:
        raise _fail(name)

    if text == "center":
        return ("center", "center")

    vertical, separator, horizontal = text.partition("-")
    if not separator or vertical not in _VERTICAL or horizontal not in _HORIZONTAL:
        raise _fail(name)
    return (vertical, horizontal)


def canonical(name: str | None) -> str:
    """The one spelling of a position, for reporting it back.

    ``ToolResult.details`` travels into ``--json`` and into logs, so what a tool
    reports must be a name the user could type again. Without this, ``center``
    would come back as ``center-center`` -- which is not one of :data:`NAMES`
    and would be rejected if fed back in.
    """
    vertical, horizontal = parse(name)
    if vertical == "center" and horizontal == "center":
        return "center"
    return f"{vertical}-{horizontal}"


def place(
    name: str | None,
    *,
    page_width: float,
    page_height: float,
    content_width: float,
    content_height: float,
    margin: float = DEFAULT_MARGIN,
) -> tuple[float, float]:
    """The lower-left corner at which content of that size sits in that cell.

    PDF user space puts the origin at the bottom-left of the page and counts
    upwards, so "top" is the *high* y value. Getting that backwards is the
    classic mistake here, and it is silent — the content lands on the page, just
    at the other end of it.

    Edge cells are inset by ``margin``; centre cells ignore it, because a margin
    is a distance from an edge and a centred thing is not near one.
    """
    vertical, horizontal = parse(name)

    if horizontal == "left":
        x = margin
    elif horizontal == "right":
        x = page_width - content_width - margin
    else:
        x = (page_width - content_width) / 2

    if vertical == "bottom":
        y = margin
    elif vertical == "top":
        y = page_height - content_height - margin
    else:
        y = (page_height - content_height) / 2

    return (x, y)


def _fail(name: str | None) -> InvalidParameterError:
    return InvalidParameterError(
        f"{name!r} is not a position.",
        remedy=f"Use one of: {', '.join(NAMES)}.",
        context={"parameter": "position", "position": name},
    )


__all__ = ["DEFAULT_MARGIN", "NAMES", "canonical", "parse", "place"]
