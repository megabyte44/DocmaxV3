"""Vercel entrypoint for the Cloud Engine.

Vercel's zero-config Python/FastAPI preset only looks for an ``app`` instance
at the project root or inside ``src/``/``app/`` — never inside ``api/``. An
earlier version of this file lived at ``api/index.py``; the preset detected
FastAPI (from ``requirements.txt``) but couldn't find a supported entrypoint,
and the deployed function crashed on the first request instead of failing at
build time. This is that fix.

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

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from docmax.server.app import create_app

app = create_app()
