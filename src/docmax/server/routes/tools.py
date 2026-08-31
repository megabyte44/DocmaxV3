"""``POST /v1/tools/{tool_name}`` — the endpoint that does the work.

Two request shapes reach the same handler, because the contract gives them the
same path: a multipart body carrying the document, and a JSON body naming a
``file_id`` that was uploaded earlier. Which one a client sends is decided by
size, and the answer has the same shape either way — a job, finished or running.

The idempotency check comes first, before anything is read. A retry after a
dropped connection must return the original job rather than run the work a
second time, and the cheapest place to guarantee that is before the bytes are
even looked at.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

# Starlette's, not FastAPI's subclass of it: ``request.form()`` produces the
# base class, so an isinstance check against the subclass rejects every real
# upload. The two are easy to confuse and the failure looks like a client bug.
from starlette.datastructures import UploadFile

from docmax.core.errors import CloudPayloadTooLargeError, InvalidParameterError
from docmax.server.parsing import parse_params, read_json
from docmax.server.security import require_api_key

router = APIRouter(prefix="/tools", tags=["tools"], dependencies=[Depends(require_api_key)])

IDEMPOTENCY_HEADER = "Idempotency-Key"


@router.post("/{tool_name}")
async def run_tool(
    tool_name: str, request: Request, api_key: str = Depends(require_api_key)
) -> JSONResponse:
    """Start (and possibly finish) one operation.

    200 when the work is already done, 202 when the caller should poll — the
    same distinction the client uses to decide whether to start polling.
    """
    state = request.app.state
    spec = state.runner.resolve(tool_name)

    idempotency_key = request.headers.get(IDEMPOTENCY_HEADER)
    if idempotency_key:
        # Scoped to `api_key`: an `Idempotency-Key` is a value the *client*
        # chooses, and without this a colliding value from a different caller
        # would hand back that caller's job, including its output's file id.
        existing = state.jobs.find_by_idempotency_key(idempotency_key, owner=api_key)
        if existing is not None:
            return JSONResponse(status_code=200, content=existing.to_payload())

    payload, filename, params, file_id = await _read_submission(request, owner=api_key)

    job = state.jobs.create(spec.name, file_id=file_id, params=params, owner=api_key)
    if idempotency_key:
        state.jobs.remember_idempotency_key(idempotency_key, job, owner=api_key)

    state.runner.start(
        job,
        payload,
        filename=filename,
        # The request's own base URL, so a server reachable by two names hands
        # each caller a URL on the name they used.
        base_url=str(request.base_url),
        storage=state.storage,
        owner=api_key,
    )
    return JSONResponse(
        status_code=200 if job.status.is_terminal else 202,
        content=job.to_payload(),
    )


async def _read_submission(
    request: Request, *, owner: str
) -> tuple[bytes, str, dict[str, Any], str | None]:
    """Bytes, filename, parameters, and the file id if there was one."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        return await _from_multipart(request)
    return await _from_file_id(request, owner=owner)


async def _from_multipart(request: Request) -> tuple[bytes, str, dict[str, Any], str | None]:
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise InvalidParameterError(
            "Attach the document as the 'file' part of the form.",
            remedy="Or upload it first and send its file_id as JSON.",
        )

    payload = await upload.read()
    limit = request.app.state.settings.max_sync_bytes
    if len(payload) > limit:
        raise CloudPayloadTooLargeError(
            f"That document is {len(payload)} bytes; this path accepts {limit}.",
            remedy="Request an upload URL from /v1/uploads for documents this size.",
            context={"size_bytes": len(payload), "limit": limit},
        )

    return payload, upload.filename or "document", parse_params(form.get("params")), None


async def _from_file_id(
    request: Request, *, owner: str
) -> tuple[bytes, str, dict[str, Any], str | None]:
    body = await read_json(request)
    file_id = body.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        raise InvalidParameterError(
            "Send the document as a multipart 'file' part, or name a 'file_id'.",
        )

    # Both calls check `owner` against the id's own reservation, in the same
    # lookup that finds it: a caller naming a `file_id` it did not upload gets
    # the identical "no such id" error an unknown id would raise.
    storage = request.app.state.storage
    payload: bytes = storage.get(file_id, owner=owner)
    filename: str = storage.filename(file_id, owner=owner)
    return payload, filename, parse_params(body.get("params")), file_id


__all__ = ["router"]
