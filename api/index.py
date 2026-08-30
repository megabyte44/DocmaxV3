"""Vercel entrypoint for the Cloud Engine.

Vercel's Python runtime looks for an ASGI ``app`` here and calls it directly —
it never runs ``python -m docmax.server`` or imports ``uvicorn``, so
``server/__main__.py`` is not part of this path at all.

The server package is deliberately excluded from the built wheel (see
``pyproject.toml``'s ``packages.find.exclude``) because it ships from a
checkout, not from PyPI. Vercel deploys from this checkout too, so importing
straight from ``src/`` — rather than depending on an installed ``docmax``
package — sidesteps that exclusion instead of fighting it.

``create_app()`` defaults to ``InMemoryStorage`` and ``InMemoryJobStore``
(see ``server/app.py``). Vercel functions are stateless and short-lived, so
uploads and job state will not survive between invocations under those
defaults — wire in real backends via ``create_app(storage=..., job_store=...)``
before relying on this for anything beyond a smoke test.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from docmax.server.app import create_app  # noqa: E402

app = create_app()
