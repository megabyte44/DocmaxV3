"""Bearer token authentication.

An API key is required from day one. Anonymous access would make cost and abuse
unbounded for a single-maintainer service, and a server that accepts documents
from anyone is a liability rather than a feature.

The check is deliberately boring — a dependency the routers declare once, so no
individual endpoint can forget it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Imported at runtime, not under TYPE_CHECKING: FastAPI resolves a dependency's
# annotations to decide what to inject, and an annotation it cannot resolve
# becomes a query parameter named "request" — which turns every authenticated
# endpoint into a 422 before the handler is ever reached.
from fastapi import Request

from docmax.core.branding import CLI_NAME
from docmax.core.errors import CloudAuthError

if TYPE_CHECKING:
    from docmax.server.identity import IdentityStore

REMEDY = f"Send an Authorization: Bearer header. Run `{CLI_NAME} cloud login` to configure one."


def require_api_key(request: Request) -> str:
    """Return the caller's identity, or raise the error the client understands.

    ``CloudAuthError`` becomes a 401 with ``code: cloud.auth``, which the client
    maps straight back to ``CloudAuthError`` — so the user sees the same message
    they would see if the local config were wrong.

    Two backends, checked in order — [ADR 0037](../../../docs/adr/0037-server-token-identity.md):
    the static ``api_keys`` set first (an in-memory comparison, no I/O), then
    ``app.state.identity`` if one is configured. A key from the static set
    resolves to a *degenerate user whose id is the key itself*, which is
    exactly what this function returned before ``identity`` existed — the
    return value has always been "the caller's identity", it simply had only
    one possible source until now.
    """
    header: str = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")

    if scheme.lower() != "bearer" or not token.strip():
        raise CloudAuthError("No API key was presented.", remedy=REMEDY)
    token = token.strip()

    if token in request.app.state.settings.api_keys:
        return token

    identity: IdentityStore | None = getattr(request.app.state, "identity", None)
    if identity is not None:
        user_id = identity.verify(token)
        if user_id is not None:
            return user_id

    # Same message whether the token is unknown, revoked, or the server has
    # none configured: which of those it is, is not the caller's business.
    raise CloudAuthError("The API key was not accepted.", remedy=REMEDY)


__all__ = ["require_api_key"]
