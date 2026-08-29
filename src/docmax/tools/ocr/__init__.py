"""Make a scanned document searchable.

The reference tool for the dual-engine shape, and the last of the nineteen to
be implemented. Local OCR means Tesseract, its language packs, Poppler and
OpenCV — the single most painful install in the project, and therefore exactly
the case the Cloud Engine exists for. The local engine is the default and the
full-featured one; cloud is the way to run this today without spending an
afternoon on dependencies first.

Both engines produce the same thing: the document, page for page, with an
invisible text layer over the pages that needed one. Both are checked by the
same validators.
"""

from __future__ import annotations
