"""Turn the wire error envelope back into a typed exception.

``docs/cloud-api.md`` specifies that every failure returns the same envelope and
that its ``code`` maps 1:1 onto this hierarchy. This module is the half of that
mapping the client owns; ``server/errors.py`` owns the other half. Keeping them
symmetrical is what lets a caller write one ``except CloudQuotaExceededError``
and have it mean the same thing regardless of which endpoint answered.

The code is trusted ahead of the HTTP status, because it is more specific: a
422 could be any member of the input family, and only the code says which.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NoReturn

from docmax.core.errors import (
    CloudAuthError,
    CloudEngineUnavailableError,
    CloudPayloadTooLargeError,
    CloudProtocolError,
    CloudQuotaExceededError,
    CloudServerError,
    CloudTimeoutError,
    CorruptDocumentError,
    DocMaxError,
    EncryptedDocumentError,
    EngineNotSupportedError,
    ErrorCode,
    InputNotFoundError,
    InvalidParameterError,
    LicenseRequiredError,
    UnsupportedFormatError,
)

_BY_CODE: dict[str, type[DocMaxError]] = {
    code.value: exception
    for code, exception in {
        ErrorCode.CLOUD_UNAVAILABLE: CloudEngineUnavailableError,
        ErrorCode.CLOUD_AUTH: CloudAuthError,
        ErrorCode.CLOUD_QUOTA: CloudQuotaExceededError,
        ErrorCode.CLOUD_PAYLOAD_TOO_LARGE: CloudPayloadTooLargeError,
        ErrorCode.CLOUD_TIMEOUT: CloudTimeoutError,
        ErrorCode.CLOUD_SERVER: CloudServerError,
        ErrorCode.CLOUD_PROTOCOL: CloudProtocolError,
        ErrorCode.INPUT_NOT_FOUND: InputNotFoundError,
        ErrorCode.INPUT_UNSUPPORTED_FORMAT: UnsupportedFormatError,
        ErrorCode.INPUT_CORRUPT: CorruptDocumentError,
        ErrorCode.INPUT_ENCRYPTED: EncryptedDocumentError,
        ErrorCode.INPUT_INVALID_PARAMETER: InvalidParameterError,
        ErrorCode.ENGINE_NOT_SUPPORTED: EngineNotSupportedError,
        ErrorCode.LICENSE_REQUIRED: LicenseRequiredError,
    }.items()
}

#: Fallback when the body carries no usable code — a proxy's 502 HTML page, or
#: a server that answers with a bare status.
_BY_STATUS: dict[int, type[DocMaxError]] = {
    401: CloudAuthError,
    403: CloudAuthError,
    413: CloudPayloadTooLargeError,
    422: InvalidParameterError,
    429: CloudQuotaExceededError,
}


def raise_for_error(status_code: int, payload: object) -> NoReturn:
    """Raise the typed error this response describes. Never returns."""
    envelope: Mapping[str, Any] = {}
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping):
            envelope = error

    code = envelope.get("code")
    exception = _BY_CODE.get(code) if isinstance(code, str) else None
    if exception is None:
        exception = _BY_STATUS.get(status_code)
    if exception is None:
        exception = CloudServerError if status_code >= 500 else CloudEngineUnavailableError

    message = envelope.get("message")
    if not isinstance(message, str) or not message:
        message = f"The cloud endpoint returned HTTP {status_code}."

    remedy = envelope.get("remedy")
    raise exception(
        message,
        remedy=remedy if isinstance(remedy, str) else None,
        context={"http_status": status_code},
    )


__all__ = ["raise_for_error"]
