"""Parsing the page selections users type.

Four M2 tools take a page selection, and they must all read it the same way: a
user who learns `1-3,7` for `pages` should not discover that `rotate` wants
something else. So it is parsed once, here.

Private to ``tools`` — the leading underscore, and no tool package of its own,
so the registry's directory walk never sees it. It is not in ``core`` because
``core`` owns contracts every layer speaks, and this is a convenience two dozen
tools will share among themselves.

## The syntax

```
1          a single page
1-3        an inclusive range
5-         page 5 to the end
-4         the start to page 4
1-3,7,9-   any of the above, comma-separated
all        every page (also the default when nothing is given)
```

Pages are **1-based and inclusive**, because that is what the user sees in their
viewer. Everything downstream is 0-based, and :func:`resolve` is the single
place that conversion happens.

Every failure is :class:`InvalidParameterError`, raised *eagerly* — before a
parser is opened or a temp file created. ``errors.py`` records why: v2 called
bare ``int()`` on user page ranges, so ``--pages 1-a`` surfaced a ``ValueError``
traceback partway through an operation that had already started writing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docmax.core.errors import InvalidParameterError

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Accepted spellings of "everything". Checked case-insensitively.
_ALL = frozenset({"", "all", "*"})


def _fail(message: str, *, remedy: str, spec: str) -> InvalidParameterError:
    return InvalidParameterError(message, remedy=remedy, context={"pages": spec})


def _bound(raw: str, *, spec: str) -> int:
    """One integer from a range endpoint, or a typed error naming what was wrong."""
    text = raw.strip()
    if not text.isdigit():
        raise _fail(
            f"{text!r} is not a page number in {spec!r}.",
            remedy="Use numbers, like 1-3,7. Pages start at 1.",
            spec=spec,
        )
    page = int(text)
    if page < 1:
        raise _fail(
            f"Page numbers start at 1, but {spec!r} asks for {page}.",
            remedy="Use 1 for the first page.",
            spec=spec,
        )
    return page


def parse(spec: str | None, *, total: int) -> tuple[int, ...]:
    """Turn a selection into 0-based page indices, in the order written.

    Order is preserved and duplicates are kept: ``3,1,3`` means page three, then
    one, then three again. Tools that need a permutation — ``reorder`` — check
    that themselves, because for ``pages`` repeating a page is a legitimate
    request rather than a mistake.

    ``total`` is required so that an out-of-range page is refused here, with a
    message naming the document's actual length, rather than by an ``IndexError``
    somewhere inside a writer.
    """
    if total < 1:
        raise _fail(
            "The document has no pages.",
            remedy="Check the file opens in a viewer.",
            spec=spec or "all",
        )

    text = (spec or "").strip()
    if text.lower() in _ALL:
        return tuple(range(total))

    pages: list[int] = []
    for chunk in text.split(","):
        term = chunk.strip()
        if not term:
            raise _fail(
                f"Empty page range in {spec!r}.",
                remedy="Remove the stray comma, as in 1-3,7.",
                spec=text,
            )

        if "-" not in term:
            pages.append(_bound(term, spec=text))
            continue

        if term.count("-") > 1:
            raise _fail(
                f"{term!r} is not a page range.",
                remedy="Write a range as 1-3.",
                spec=text,
            )

        start_text, _, end_text = term.partition("-")
        start = _bound(start_text, spec=text) if start_text.strip() else 1
        end = _bound(end_text, spec=text) if end_text.strip() else total

        if start > end:
            raise _fail(
                f"{term!r} counts backwards.",
                remedy=f"Write it as {end}-{start}, or list the pages you want.",
                spec=text,
            )
        pages.extend(range(start, end + 1))

    out_of_range = sorted({page for page in pages if page > total})
    if out_of_range:
        listed = ", ".join(str(page) for page in out_of_range)
        raise _fail(
            f"The document has {total} page(s); {spec!r} asks for {listed}.",
            remedy=f"Use a page between 1 and {total}.",
            spec=text,
        )

    if not pages:
        raise _fail(
            f"{spec!r} selects no pages.",
            remedy="Select at least one page.",
            spec=text,
        )

    return tuple(page - 1 for page in pages)


def describe(indices: Sequence[int]) -> str:
    """A short human summary of a selection, for progress and results.

    Collapses runs, so forty consecutive pages read as ``1-40`` rather than as
    forty numbers in a progress line.
    """
    if not indices:
        return "no pages"

    runs: list[tuple[int, int]] = []
    for index in indices:
        page = index + 1
        if runs and page == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], page)
        else:
            runs.append((page, page))

    return ",".join(str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in runs)


__all__ = ["describe", "parse"]
