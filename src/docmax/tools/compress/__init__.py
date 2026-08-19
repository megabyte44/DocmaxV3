"""Shrink a PDF with Ghostscript.

The reference for tools whose engine is an **external program** rather than a
Python library: the output is written by something else, so it goes through
``atomic_path`` instead of ``atomic_write``, and the binary has to be located,
version-reported, and killed if the user cancels.
"""

from __future__ import annotations
