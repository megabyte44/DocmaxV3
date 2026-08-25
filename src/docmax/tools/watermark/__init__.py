"""Draw text across the pages of a PDF.

The text is drawn as real PDF text operators over the existing content — not
rasterised, and not stitched in from a second document. ``local.py`` says what
that costs and what it does not do.
"""

from __future__ import annotations
