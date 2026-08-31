"""``GET /v1/jobs/{job_id}`` — how is it going.

A failed job answers 200 with ``status: failed`` and the error envelope inside
it: the *request* succeeded, the work did not, and conflating the two would make
a client unable to tell a broken job from a broken connection. The client reads
the status, sees ``failed``, and raises the error the envelope names.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from docmax.server.security import require_api_key

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_api_key)])


@router.get("/{job_id}")
async def get_job(
    job_id: str, request: Request, key: str = Depends(require_api_key)
) -> dict[str, Any]:
    """Report one job. Unknown ids — and jobs owned by someone else — are a 404.

    ``jobs.get`` checks ``key`` against the job's own record in the same
    lookup that finds it: a caller holding a valid key for this endpoint, but
    not the one that created this job, sees the identical "no such job" a
    made-up id would produce. See ADR 0035.
    """
    job = request.app.state.jobs.get(job_id, owner=key)
    payload: dict[str, Any] = job.to_payload()
    return payload


__all__ = ["router"]
