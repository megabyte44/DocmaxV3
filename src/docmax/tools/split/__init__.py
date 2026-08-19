"""Split one PDF into many.

The reference for **multi-output** tools: everything else in M2 produces a single
file, and this is the one that stages a whole directory and swaps it in as a
unit. See ``local.py`` and ``core/atomic.py``'s ``atomic_dir``.
"""

from __future__ import annotations
