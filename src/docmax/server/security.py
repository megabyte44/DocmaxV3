"""Bearer token authentication.

An API key is required from day one. Anonymous access would make cost and abuse
unbounded for a single-maintainer service, and a server that accepts documents
from anyone is a liability rather than a feature.

The check is deliberately boring — a dependency the routers declare once, so no
individual endpoint can forget it.
"""

from __future__ import annotations

# Imported at runtime, not under TYPE_CHECKING: FastAPI resolves a dependency's
# annotations to decide what to inject, and an annotation it cannot resolve
# becomes a query parameter named "request" — which turns every authenticated
# endpoint into a 422 before the handler is ever reached.
from fastapi import Request

from docmax.core.branding import CLI_NAME
from docmax.core.errors import CloudAuthError

REMEDY = f"Send an Authorization: Bearer header. Run `{CLI_NAME} cloud login` to configure one."


def require_api_key(request: Request) -> str:
    """Return the presented key, or raise the error the client understands.

    ``CloudAuthError`` becomes a 401 with ``code: cloud.auth``, which the client
    maps straight back to ``CloudAuthError`` — so the user sees the same message
    they would see if the local config were wrong.
    """
    header: str = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")

    if scheme.lower() != "bearer" or not token.strip():
        raise CloudAuthError("No API key was presented.", remedy=REMEDY)

    accepted = request.app.state.settings.api_keys
    if token.strip() not in accepted:
        # Same message whether the key is unknown or the server has none
        # configured: which of the two it is, is not the caller's business.
        raise CloudAuthError("The API key was not accepted.", remedy=REMEDY)

    return token.strip()


__all__ = ["require_api_key"]
