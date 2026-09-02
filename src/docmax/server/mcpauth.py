"""Bearer-token verification for the remote MCP route, and nothing more.

[ADR 0035](../../../docs/adr/0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md)
is explicit that this route adds no new auth model: the same bearer tokens
`security.require_api_key` already checks for every REST route are the whole
identity model here too, now resolved through the same two backends —
[ADR 0037](../../../docs/adr/0037-server-token-identity.md). The MCP SDK's
HTTP transport expects a ``TokenVerifier`` — an OAuth-shaped protocol
(``verify_token`` returns scopes, an issuer, a subject) — because that is the
shape a *real* OAuth resource server would plug in. Nothing here implements
OAuth: there is no issuer, no token endpoint, no dynamic client registration,
and ``ApiKeyVerifier`` answers the same question ``require_api_key`` does, in
the shape the transport needs to plug into its own session machinery.

**This is what buys session/auth binding for free.** The SDK's
``StreamableHTTPSessionManager`` records which verified identity created each
MCP session and refuses a later request on that session from a different one
— a mismatch gets the same "Session not found" a made-up session id would, not
a hint that the session exists but belongs to someone else. That identity is
the ``AccessToken.client_id`` this module returns — the caller's resolved user
id, exactly what `require_api_key` returns for the REST routes: two different
tokens belonging to the same user are the same identity here, matching that
side rather than diverging from it now that a token and its owner can differ.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mcp.server.auth.provider import AccessToken, TokenVerifier

if TYPE_CHECKING:
    from docmax.server.identity import IdentityStore


@dataclass(slots=True)
class ApiKeyVerifier(TokenVerifier):
    """Accepts the keys `ServerSettings.api_keys` accepts, then `identity`.

    Mirrors `security.require_api_key`'s order exactly: the static set first
    — a plain membership test, no I/O — and a token from it resolves to a
    degenerate user whose id is the key itself, precisely what
    ``AccessToken.client_id`` was before ``identity`` existed. `identity`,
    when configured, is consulted second and returns the user id a durable,
    revocable token belongs to. Both paths feed the identical ``owner`` value
    into `Storage` and `JobStore` — one identity model, checked the same way,
    on every path a caller can reach.
    """

    accepted: frozenset[str]
    identity: IdentityStore | None = None

    async def verify_token(self, token: str) -> AccessToken | None:
        if token in self.accepted:
            return AccessToken(token=token, client_id=token, scopes=[])
        if self.identity is not None:
            user_id = self.identity.verify(token)
            if user_id is not None:
                return AccessToken(token=token, client_id=user_id, scopes=[])
        return None


__all__ = ["ApiKeyVerifier"]
