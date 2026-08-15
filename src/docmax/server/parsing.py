"""Turning request bodies into things the rest of the server can trust.

Every failure in here is an :class:`InvalidParameterError`, which the envelope
renders as a 422 with ``code: input.invalid_parameter``. That matters more than
it looks: a malformed request must produce the same shape of answer as a
malformed page range typed at the terminal, so a caller has one thing to parse
rather than two.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from docmax.core.errors import InvalidParameterError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import Request


async def read_json(request: Request) -> Mapping[str, Any]:
    """Decode a JSON object body, or say precisely what was wrong with it."""
    try:
        body = await request.json()
    except ValueError as exc:
        raise InvalidParameterError(
            "The request body is not valid JSON.",
            remedy="Send a JSON object, or use multipart/form-data to attach a file.",
        ) from exc

    if not isinstance(body, dict):
        raise InvalidParameterError(
            f"Expected a JSON object, got {type(body).__name__}.",
        )
    return body


def parse_params(raw: object) -> dict[str, Any]:
    """Read the ``params`` field, which arrives as JSON text in a form field.

    Validation against the tool's own ``ParamSpec`` happens after this, in the
    registry — the same validation the CLI applies to argv, so an invalid
    parameter is rejected identically whichever door it came in through.
    """
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str):
        raise InvalidParameterError(
            f"Expected 'params' to be a JSON object, got {type(raw).__name__}.",
        )

    try:
        decoded = json.loads(raw)
    except ValueError as exc:
        raise InvalidParameterError("The 'params' field is not valid JSON.") from exc

    if not isinstance(decoded, dict):
        raise InvalidParameterError(
            f"Expected 'params' to be a JSON object, got {type(decoded).__name__}.",
        )
    return decoded


__all__ = ["parse_params", "read_json"]
