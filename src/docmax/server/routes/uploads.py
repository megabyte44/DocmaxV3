"""The presigned-upload path, for documents too large to post in one request.

The contract sends large uploads straight to storage and never proxies them
through the API. This reference server has no separate storage host, so the
"presigned" URL points back at itself — the shape of the exchange is identical,
which is what matters for a client implementing against it.

One deliberate difference from a real presigned URL: this one is not a bearer
token of its own, so the PUT still carries the API key. A deployment backed by
object storage hands out a genuinely presigned URL instead, and the client is
already written not to send its API key to a storage host.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from docmax.core.errors import CloudPayloadTooLargeError, InvalidParameterError
from docmax.server.parsing import read_json
from docmax.server.security import require_api_key

router = APIRouter(prefix="/uploads", tags=["uploads"], dependencies=[Depends(require_api_key)])

#: How long a ticket is good for. Short: it is permission to write into this
#: endpoint's storage, and it should not outlive the upload it was issued for.
TICKET_LIFETIME_SECONDS = 900


@router.post("")
async def create_upload(request: Request, key: str = Depends(require_api_key)) -> dict[str, Any]:
    """Reserve an id and hand back somewhere to put the bytes."""
    body = await read_json(request)
    filename = body.get("filename")
    size_bytes = body.get("size_bytes")

    if not isinstance(filename, str) or not filename:
        raise InvalidParameterError("A 'filename' is required.")
    if not isinstance(size_bytes, int) or size_bytes < 0:
        raise InvalidParameterError("A non-negative integer 'size_bytes' is required.")

    storage = request.app.state.storage
    file_id = storage.reserve(filename=filename, size_bytes=size_bytes, owner=key)

    base = str(request.base_url).rstrip("/")
    return {
        "upload_url": f"{base}/v1/uploads/{file_id}",
        "file_id": file_id,
        "expires_in": TICKET_LIFETIME_SECONDS,
    }


@router.put("/{file_id}")
async def receive_upload(
    file_id: str, request: Request, key: str = Depends(require_api_key)
) -> dict[str, Any]:
    """Accept the bytes for a reserved id.

    Reads the body into memory, which is honest about what the reference
    backend is: an object-store deployment never sees these bytes at all.
    """
    settings = request.app.state.settings
    payload = await request.body()

    if len(payload) > settings.max_upload_bytes:
        raise CloudPayloadTooLargeError(
            f"That upload is {len(payload)} bytes; this endpoint accepts "
            f"{settings.max_upload_bytes}.",
            context={"size_bytes": len(payload), "limit": settings.max_upload_bytes},
        )

    # `storage.put` checks `key` against the id's own reservation, in the same
    # lookup that finds it -- a second caller cannot fill in bytes for a
    # `file_id` it did not reserve, whether or not it could guess one.
    request.app.state.storage.put(file_id, payload, owner=key)
    return {"ok": True, "file_id": file_id, "size_bytes": len(payload)}


__all__ = ["router"]
