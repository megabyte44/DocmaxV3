"""Rasterise the pages of a PDF into image files.

Poppler's ``pdftoppm`` does the drawing and writes the files itself, so this
tool needs no Python imaging library at all -- see ``local.py``.
"""

from __future__ import annotations
