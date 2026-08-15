"""Make a scanned document searchable.

The reference tool for the dual-engine shape. Local OCR means Tesseract, its
language packs, Poppler, and OpenCV — the single most painful install in the
project, and therefore exactly the case the Cloud Engine exists for. The local
engine remains the default and the full-featured one; cloud is the way to run
this today without spending an afternoon on dependencies first.
"""

from __future__ import annotations
