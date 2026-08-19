"""The page selection four tools share.

Parsed in one place so a user who learns `1-3,7` for `pages` does not discover
that `rotate` wants something else. Most of these tests are about *refusals*:
`errors.py` records that v2 called bare `int()` on user page ranges, so
`--pages 1-a` surfaced a traceback from partway through an operation that had
already begun writing.
"""

from __future__ import annotations

import pytest

from docmax.core.errors import InvalidParameterError
from docmax.tools._pagespec import describe, parse

# ---------------------------------------------------------------------------
# Accepting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("1", (0,)),
        ("3", (2,)),
        ("1-3", (0, 1, 2)),
        ("2-2", (1,)),
        ("1,3,5", (0, 2, 4)),
        ("1-2,4", (0, 1, 3)),
        ("4-", (3, 4)),
        ("-2", (0, 1)),
        ("1-5", (0, 1, 2, 3, 4)),
    ],
)
def test_selections_are_parsed(spec: str, expected: tuple[int, ...]) -> None:
    assert parse(spec, total=5) == expected


@pytest.mark.parametrize("spec", [None, "", "  ", "all", "ALL", "*"])
def test_everything_means_every_page(spec: str | None) -> None:
    assert parse(spec, total=3) == (0, 1, 2)


def test_pages_are_one_based() -> None:
    """What the user sees in their viewer. The 0-based conversion happens here."""
    assert parse("1", total=3) == (0,)


def test_order_is_preserved() -> None:
    """`3,1` means page three then page one — the tools rely on this for reorder."""
    assert parse("3,1", total=3) == (2, 0)


def test_repeats_are_kept() -> None:
    """`pages` may legitimately extract the same page twice; `reorder` rejects it itself."""
    assert parse("1,1", total=3) == (0, 0)


def test_whitespace_is_tolerated() -> None:
    assert parse(" 1 - 2 , 3 ", total=3) == (0, 1, 2)


# ---------------------------------------------------------------------------
# Refusing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        ("1-a", "not a page number"),
        ("a", "not a page number"),
        ("0", "start at 1"),
        ("1-2-3", "not a page range"),
        ("1,,2", "Empty page range"),
        ("3-1", "counts backwards"),
    ],
)
def test_malformed_selections_are_refused(spec: str, fragment: str) -> None:
    with pytest.raises(InvalidParameterError) as caught:
        parse(spec, total=5)

    assert fragment in str(caught.value)
    assert caught.value.remedy, "every refusal names the next step"


def test_a_page_past_the_end_names_the_length() -> None:
    """ "Page 9 does not exist" is useless without saying how long the document is."""
    with pytest.raises(InvalidParameterError) as caught:
        parse("9", total=3)

    message = str(caught.value)
    assert "3 page(s)" in message
    assert "9" in message


def test_a_range_past_the_end_is_refused() -> None:
    with pytest.raises(InvalidParameterError):
        parse("1-9", total=3)


def test_an_open_range_stops_at_the_end() -> None:
    """`4-` is not out of range; it means "to the end"."""
    assert parse("4-", total=5) == (3, 4)


def test_a_document_with_no_pages_is_refused() -> None:
    with pytest.raises(InvalidParameterError):
        parse("1", total=0)


def test_the_failure_carries_the_spec_for_the_error_context() -> None:
    with pytest.raises(InvalidParameterError) as caught:
        parse("1-a", total=5)

    assert caught.value.context["pages"] == "1-a"


# ---------------------------------------------------------------------------
# Describing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("indices", "expected"),
    [
        ((), "no pages"),
        ((0,), "1"),
        ((0, 1, 2), "1-3"),
        ((0, 2), "1,3"),
        ((0, 1, 2, 5), "1-3,6"),
        ((2, 1, 0), "3,2,1"),
    ],
)
def test_runs_are_collapsed(indices: tuple[int, ...], expected: str) -> None:
    """Forty consecutive pages read as `1-40`, not as forty numbers in a progress line."""
    assert describe(indices) == expected
