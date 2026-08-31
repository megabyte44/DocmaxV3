"""``GET /v1/outputs/{file_id}`` — collecting a finished document.

The contract describes a job's output as an opaque ``url``, because a real
deployment hands out a presigned link to object storage and the API never sees
the bytes. This reference server has no separate storage host, so the URL points
back at itself — the shape of the exchange is identical, which is what a client
implementing against this needs.

**This route deliberately takes no API key**, and that is the one thing here
worth pausing over. ``CloudClient.fetch_output`` downloads through a *bare*
client, with the comment that a presigned URL points at storage and "our bearer
token has no business being sent there". That is correct, and it means an
authenticated download route would be unreachable by a conforming client.

What stands in for the signature is the file id: 32 hex characters from
``secrets``, unguessable, and issued only to the caller whose job produced it.
That is weaker than a real presigned URL — it does not expire on its own and it
is not bound to a method or a clock. The mitigation is that the storage backend
reaps outputs (``retention_seconds``), and the honest statement is that a
deployment wanting more should put object storage behind it, which is the
arrangement the contract was written for.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

router = APIRouter(prefix="/outputs", tags=["outputs"])


@router.get("/{file_id}")
async def read_output(file_id: str, request: Request) -> Response:
    """Hand back the bytes of a finished job.

    ``storage.get`` raises :class:`InputNotFoundError` for an id that has been
    reaped or never existed, which the app's exception handler renders as the
    contract's error envelope — so an expired output is a typed 404 rather than
    a stack trace.

    ``owner=None`` because this route takes no API key at all (see the module
    docstring) — there is no caller identity to check a `file_id` against
    here, by design. ``Storage`` still records who *produced* it; nothing on
    this route reads that field, deliberately.
    """
    payload = request.app.state.storage.get(file_id, owner=None)
    return Response(
        content=payload,
        media_type=_content_type(payload),
        headers={"Content-Length": str(len(payload))},
    )


def _content_type(payload: bytes) -> str:
    """The media type, from the bytes rather than from a filename.

    Deliberately narrow. The server could guess from an extension, but it does
    not choose the output's name — the tool does, into a destination the server
    picked — so an extension here would be the server's own guess reported back
    as fact.

    A client never relies on this: it writes the bytes to the path the *user*
    asked for. So being right about PDF and honest about everything else beats
    a mime table that is wrong for a `.docx` the server never saw named.

    A zip signature is included because a directory-producing tool's output
    *is* a zip archive on the wire (ADR 0034) — as opposed to `.docx`/`.odt`/
    `.epub`, which are zip containers of something more specific and are left
    to fall through to the generic case, exactly as before.
    """
    if payload.startswith(b"%PDF-"):
        return "application/pdf"
    if payload.startswith(b"PK\x03\x04"):
        return "application/zip"
    return "application/octet-stream"


__all__ = ["router"]
