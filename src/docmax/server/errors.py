"""Typed exception in, error envelope out.

The mirror image of ``cloud_client/errors.py``. Keeping the two symmetrical is
what makes the contract worth writing down: the server raises the same error
classes the CLI raises locally, they are serialised here, and the client turns
them back into the same class on the other side. A quota failure is
``CloudQuotaExceededError`` in all three places.

The consequence worth stating: an anticipated failure produces an envelope with
a remedy, never a stack trace and never an HTML error page. That is the same
promise the terminal makes, kept over HTTP.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.core.errors import DocMaxError, ErrorCode, InternalError

if TYPE_CHECKING:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse as JSONResponseType

_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.CLOUD_AUTH: 401,
    ErrorCode.CLOUD_QUOTA: 429,
    ErrorCode.CLOUD_PAYLOAD_TOO_LARGE: 413,
    ErrorCode.CLOUD_TIMEOUT: 504,
    ErrorCode.CLOUD_SERVER: 500,
    # Not 422: a job id or a file id that does not exist is a missing resource,
    # and answering 404 lets a caller distinguish "gone" from "malformed".
    ErrorCode.INPUT_NOT_FOUND: 404,
    ErrorCode.ENGINE_NOT_SUPPORTED: 404,
    ErrorCode.LICENSE_REQUIRED: 402,
    ErrorCode.CANCELLED: 499,
}


def http_status_for(exc: DocMaxError) -> int:
    """The status code this error should be delivered with."""
    if exc.code in _STATUS_BY_CODE:
        return _STATUS_BY_CODE[exc.code]
    if exc.code.value.startswith(ErrorCode.INPUT.value):
        return 422
    if not exc.user_fixable:
        return 500
    return 400


def error_envelope(exc: DocMaxError) -> dict[str, Any]:
    """The failure body from ``docs/cloud-api.md``, and nothing else.

    Note what is *not* here: ``context`` stays server-side. It may name paths
    and counts, and none of that is the caller's business.
    """
    return {
        "ok": False,
        "error": {
            "code": exc.code.value,
            "message": exc.message,
            "remedy": exc.remedy,
            "retryable": exc.retryable,
        },
    }


def install_error_handlers(app: FastAPI) -> None:
    """Route every exception through the envelope, including the unexpected ones."""
    from fastapi.responses import JSONResponse

    async def handle_known(request: Request, exc: Exception) -> JSONResponseType:
        known = exc if isinstance(exc, DocMaxError) else InternalError(str(exc))
        return JSONResponse(status_code=http_status_for(known), content=error_envelope(known))

    async def handle_unknown(request: Request, exc: Exception) -> JSONResponseType:
        # Anything reaching here is a bug in this server. The client still gets
        # a parseable envelope rather than a traceback or an HTML page.
        wrapped = InternalError(f"{type(exc).__name__}: {exc}")
        return JSONResponse(status_code=500, content=error_envelope(wrapped))

    app.add_exception_handler(DocMaxError, handle_known)
    app.add_exception_handler(Exception, handle_unknown)


__all__ = ["error_envelope", "http_status_for", "install_error_handlers"]
