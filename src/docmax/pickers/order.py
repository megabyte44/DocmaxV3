"""The page order picker: drag pages into a sequence, get a page spec back.

The third of ADR 0005's operations whose parameter is hard to express blind —
"what order pages should go in". Unlike the box picker this one needs no
geometry, only a count.

Returns the string ``reorder``'s ``--order`` already takes, so what comes out of
the browser and what a user types are the same value arriving by two routes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError
from docmax.pickers._pages import order_page
from docmax.pickers.server import DEFAULT_TIMEOUT_SECONDS, run_picker
from docmax.tools._pdf import open_pdf, page_count

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def pick_order(
    source: Path,
    *,
    announce: Callable[[str], None] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    open_browser: bool = True,
) -> str:
    """Open a picker on ``source`` and return the order the user arranged.

    The value is ``3,1,2``-shaped: 1-based, comma-separated, and every page
    exactly once. ``reorder`` re-checks the permutation itself — this refuses a
    malformed payload early, but the tool remains the authority on what a legal
    order is, and neither defers to the other.
    """
    total = page_total(source)

    choice = run_picker(
        document=source,
        page=order_page(name=source.name, pages=total),
        announce=announce,
        timeout=timeout,
        open_browser=open_browser,
    )

    return _order_from(choice.payload, total=total)


def page_total(source: Path) -> int:
    """How many pages ``source`` has. The only thing this module reads."""
    from docmax.core.models import DocumentRef

    return page_count(open_pdf(DocumentRef.from_path(source)))


def _order_from(payload: dict[str, Any], *, total: int) -> str:
    """A page spec from what the browser posted, or a typed error.

    Checked as a permutation here as well as in the tool, and deliberately: a
    picker that returned ``1,1,3`` for a three-page document would produce a
    confusing failure two layers away, from a value the user never typed.
    """
    raw = payload.get("order")
    if not isinstance(raw, list) or not raw:
        raise InvalidParameterError(
            "The picker returned no page order.",
            remedy="Try again, or pass --order 3,1,2 instead.",
            context={"order": str(raw)},
        )

    pages: list[int] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidParameterError(
                f"The picker returned {value!r}, which is not a page number.",
                remedy="Try again, or pass --order 3,1,2 instead.",
                context={"value": str(value)},
            )
        pages.append(value)

    if sorted(pages) != list(range(1, total + 1)):
        raise InvalidParameterError(
            f"The picker returned {len(pages)} page(s) for a {total}-page document, "
            "and every page must appear exactly once.",
            remedy="Try again, or pass --order 3,1,2 instead.",
            context={"pages": total, "returned": len(pages)},
        )

    return ",".join(str(page) for page in pages)


__all__ = ["page_total", "pick_order"]
