"""Thin HTTP client for the Cloud Engine.

Fully open source and fully optional. The endpoint is configurable, so this
client talks to the hosted service by default and to a self-hosted deployment
if you point it elsewhere.

Cloud exists for one reason: to let someone use a tool without installing its
heavy local dependencies. It is never a default and never silent — see the
consent rules in ``core/router.py`` and the wire contract in ``docs/cloud-api.md``.
"""

from __future__ import annotations
