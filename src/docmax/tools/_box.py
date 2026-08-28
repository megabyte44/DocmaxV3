"""Parsing the rectangles users type, and the ones a picker hands back.

``crop`` takes one, [ADR 0005](../../../docs/adr/0005-gui-pickers.md)'s box
picker produces one, and the CLI has to validate one before any file is opened.
Three readers of one syntax, so it is parsed once, here — for the reason
``_pagespec`` exists: a user who learns ``x,y,width,height`` for the flag must
not find the picker speaking a different dialect.

Private to ``tools``: the leading underscore, and no tool package of its own, so
the registry's directory walk never sees it.

## The syntax

```
10,10,500,700      x, y, width, height — in PDF points, 72 to the inch
```

**The origin is the bottom-left of the page**, which is the PDF coordinate
system's own. A top-left origin would read more naturally to someone counting
down from the top of a viewer, and it was rejected: ``crop`` writes these
numbers into a ``/MediaBox`` more or less unchanged, and a flip applied
somewhere in between is a transformation that is invisible in the output and
wrong in exactly one direction. The picker converts for the user, so nobody
choosing a box visually ever types a coordinate at all.

Every failure is :class:`InvalidParameterError`, raised *eagerly* — before a
document is opened or a temp file created.
"""

from __future__ import annotations

from dataclasses import dataclass

from docmax.core.errors import InvalidParameterError

#: How many numbers a box is. Named because the error message quotes it.
_FIELDS = 4


@dataclass(frozen=True, slots=True)
class PageBox:
    """A rectangle on a page, in PDF points, measured from the bottom-left."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y + self.height

    def as_spec(self) -> str:
        """The string form, which is what ``--box`` accepts and a picker returns.

        Whole numbers lose their trailing ``.0`` so that a box chosen on a
        432x648 page comes back as ``10,10,500,700`` rather than
        ``10.0,10.0,500.0,700.0``. The value round-trips through :func:`parse`
        either way; this one is just readable in a shell history.
        """
        return ",".join(_trim(value) for value in (self.x, self.y, self.width, self.height))


def _trim(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


def _fail(message: str, *, remedy: str, spec: str) -> InvalidParameterError:
    return InvalidParameterError(message, remedy=remedy, context={"box": spec})


def parse(spec: str | None) -> PageBox:
    """One :class:`PageBox` from ``x,y,width,height``, or a typed error saying why not.

    Refuses a zero or negative width or height rather than normalising it. A
    box with no area is not a smaller crop; it is a request that cannot be
    honoured, and quietly turning it into something else would produce a file
    the user did not ask for.
    """
    if spec is None or not spec.strip():
        raise _fail(
            "A crop box is required.",
            remedy="Pass --box x,y,width,height in points, or --interactive to draw one.",
            spec="",
        )

    parts = [part.strip() for part in spec.split(",")]
    if len(parts) != _FIELDS:
        raise _fail(
            f"A box needs {_FIELDS} numbers, and {spec!r} has {len(parts)}.",
            remedy="Use x,y,width,height — for example 10,10,500,700.",
            spec=spec,
        )

    values: list[float] = []
    for part in parts:
        try:
            values.append(float(part))
        except ValueError as exc:
            # Eagerly, and by name. v2 called bare int() on user input and let
            # the ValueError surface as a traceback partway through a run.
            raise _fail(
                f"{part!r} is not a number in {spec!r}.",
                remedy="Use numbers in points, like 10,10,500,700. 72 points is an inch.",
                spec=spec,
            ) from exc

    box = PageBox(*values)
    if box.width <= 0 or box.height <= 0:
        raise _fail(
            f"A box needs a positive width and height, and {spec!r} has "
            f"{_trim(box.width)}x{_trim(box.height)}.",
            remedy="The third and fourth numbers are the width and the height, not the far corner.",
            spec=spec,
        )
    return box


def require_within(box: PageBox, *, width: float, height: float) -> None:
    """Refuse a box that leaves the page.

    pypdf will happily write a ``/MediaBox`` that extends past the media it is
    cropping, and most viewers then render a band of nothing. Refusing here
    means the user is told at the boundary instead of discovering it in the
    output.
    """
    if box.x < 0 or box.y < 0 or box.right > width or box.top > height:
        raise _fail(
            f"The box {box.as_spec()} does not fit on a {_trim(width)}x{_trim(height)} point page.",
            remedy=(
                f"Keep x + width within {_trim(width)} and y + height within "
                f"{_trim(height)}. The origin is the bottom-left corner."
            ),
            spec=box.as_spec(),
        )


__all__ = ["PageBox", "parse", "require_within"]
