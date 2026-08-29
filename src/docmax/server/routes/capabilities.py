"""``GET /v1/capabilities`` — what this deployment can actually do.

Answered from the registry rather than from a list maintained here, so the
server cannot advertise a tool it does not have or omit one it does. A
self-hosted deployment that installs a third-party tool package starts offering
it without a line of configuration.

**"Can do" means can run, not was compiled in.** A tool appears only when its
*local* strategy reports itself available on this machine — the server runs the
local engine, so "can this endpoint offer `compress`?" is exactly "is Ghostscript
installed here?".

Before [ADR 0018](../../../../docs/adr/0018-capabilities-mean-runnable.md) this
asked the registry alone, and a server with no Ghostscript, Pandoc or Tesseract
advertised `ocr` — the one tool it could not perform. The client believes this
list and uses it to rule a tool out *before* uploading a document, so overstating
it turns a clean refusal into a failed upload.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from docmax.core.errors import DocMaxError
from docmax.core.models import Engine
from docmax.core.registry import ToolSpec, iter_tools
from docmax.server.config import API_VERSION
from docmax.server.security import require_api_key

router = APIRouter(tags=["discovery"], dependencies=[Depends(require_api_key)])


@router.get("/capabilities")
async def capabilities(request: Request) -> dict[str, Any]:
    """List the cloud-capable tools and this endpoint's limits.

    The client fetches this once and caches it, so an endpoint offering three
    of the five cloud tools degrades to "no cloud engine for that one here"
    rather than to a failure per call.
    """
    settings = request.app.state.settings
    return {
        "tools": sorted(spec.name for spec in iter_tools(engine=Engine.CLOUD) if _runnable(spec)),
        "max_sync_bytes": settings.max_sync_bytes,
        "api_version": API_VERSION,
    }


def _runnable(spec: ToolSpec) -> bool:
    """Can this endpoint actually perform ``spec`` right now?

    ``is_available`` is contractually cheap — a ``shutil.which`` or a
    ``find_spec``, never an import of the heavy dependency — so asking every
    cloud-capable tool costs a handful of path lookups, and the client fetches
    this once and caches it.

    A tool whose strategy cannot even be *loaded* is treated as unavailable
    rather than as a failed request: a malformed third-party tool package should
    remove one entry from this list, not take the endpoint down.
    """
    try:
        return spec.load_strategy(Engine.LOCAL).is_available()
    except DocMaxError:
        return False


__all__ = ["router"]
