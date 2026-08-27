"""Measured performance numbers, and the method behind them.

Deliberately small. Two tools, four fixtures, five runs — see
[METHODOLOGY.md](METHODOLOGY.md) for what is measured and what is not.

The rule this package exists to serve is `docs/planning/roadmap.md`'s:

> **No numbers appear in the README or anywhere else until they are measured** —
> a performance claim without a method behind it is marketing.

So nothing here invents a number. A tool whose binary is absent is *reported as
not measured* rather than omitted, because a results file with a gap in it is
honest and one with a hole where a row should be is not.

Not part of the wheel: `pyproject.toml` packages `src/` only, and this runs from
a checkout.
"""

from __future__ import annotations
