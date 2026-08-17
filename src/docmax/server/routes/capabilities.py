"""``GET /v1/capabilities`` — what this deployment can actually do.

Answered from the registry rather than from a list maintained here, so the
server cannot advertise a tool it does not have or omit one it does. A
self-hosted deployment that installs a third-party tool package starts offering
it without a line of configuration.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from docmax.core.models import Engine
from docmax.core.registry import iter_tools
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
        "tools": sorted(spec.name for spec in iter_tools(engine=Engine.CLOUD)),
        "max_sync_bytes": settings.max_sync_bytes,
        "api_version": API_VERSION,
    }


__all__ = ["router"]
