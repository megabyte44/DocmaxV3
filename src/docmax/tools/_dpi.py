"""The rasterisation resolution two tools share.

``to-images`` renders pages to files and ``ocr`` rasterises them before
recognition. Both take ``--dpi``, both need the same bounds, and both must
refuse the same nonsense — so it is parsed once, here, for the reason
``_pagespec``, ``_position``, ``_permissions``, ``_formats`` and ``_box`` exist:
a user who learns what ``--dpi`` accepts for one tool must not find that the
other spells it differently.

Private to ``tools``: the leading underscore, and no tool package of its own, so
the registry's directory walk never sees it.

## Why there is an upper bound

``--dpi 40000`` is a request that will exhaust memory or run for hours, and a
user who typed an extra zero is better served by a refusal than by a machine
that stops responding. The floor is where text stops being legible at all.

The *default* is deliberately not shared. ``to-images`` renders for viewing and
defaults to 150; ``ocr`` feeds a recogniser and defaults to 300, where Tesseract
stops losing small type. Each tool passes its own.
"""

from __future__ import annotations

from docmax.core.errors import InvalidParameterError

#: Below this, glyphs stop being distinguishable at all. Above it, one page can
#: reach hundreds of megabytes and a run looks like a hang.
MIN_DPI = 12
MAX_DPI = 1200


def parse(value: object, *, default: int, parameter: str = "dpi") -> int:
    """One resolution, or the typed error naming what was wrong with it.

    ``None`` means "not supplied" and yields ``default`` — every tool reads its
    parameters with ``params.get(name)``, and an absent option must not be
    distinguishable from an unset one.
    """
    if value is None:
        return default
    # `bool` is an `int` in Python, and `--dpi true` is not a resolution.
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParameterError(
            f"{parameter} must be a whole number, not {value!r}.",
            remedy=f"Try --{parameter} 150 for screen, or 300 for print.",
            context={"parameter": parameter},
        )
    if not MIN_DPI <= value <= MAX_DPI:
        raise InvalidParameterError(
            f"{parameter} must be between {MIN_DPI} and {MAX_DPI}, not {value}.",
            remedy=f"Try --{parameter} 150 for screen, or 300 for print.",
            context={"parameter": parameter, "min": MIN_DPI, "max": MAX_DPI},
        )
    return value


__all__ = ["MAX_DPI", "MIN_DPI", "parse"]
