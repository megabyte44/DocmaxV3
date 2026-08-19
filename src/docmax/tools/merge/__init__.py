"""Combine several PDFs into one.

The reference tool for the local-only shape: pure pypdf, no external binary, no
cloud engine. Uploading a document to perform a millisecond-long pure-Python
operation would be slower, less private, and would need a network — see
``docs/architecture/overview.md``.
"""

from __future__ import annotations
