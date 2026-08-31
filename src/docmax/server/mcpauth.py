"""Bearer-token verification for the remote MCP route, and nothing more.

[ADR 0035](../../../docs/adr/0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md)
is explicit that this route adds no new auth model: the same bearer tokens
`security.require_api_key` already checks for every REST route are the whole
identity model here too. The MCP SDK's HTTP transport expects a
``TokenVerifier`` — an OAuth-shaped protocol (``verify_token`` returns scopes,
an issuer, a subject) — because that is the shape a *real* OAuth resource
server would plug in. Nothing here implements OAuth: there is no issuer, no
token endpoint, no dynamic client registration, and ``ApiKeyVerifier`` answers
the same question ``require_api_key`` does, in the shape the transport needs
to plug into its own session machinery.

**This is what buys session/auth binding for free.** The SDK's
``StreamableHTTPSessionManager`` records which verified identity created each
MCP session and refuses a later request on that session from a different one
— a mismatch gets the same "Session not found" a made-up session id would, not
a hint that the session exists but belongs to someone else. That identity is
the ``AccessToken.client_id`` this module returns, which is simply the raw key
string: two different keys are two different identities, one key reused is
the same identity, exactly matching ``require_api_key``'s own notion of who is
calling.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp.server.auth.provider import AccessToken, TokenVerifier


@dataclass(slots=True)
class ApiKeyVerifier(TokenVerifier):
    """Accepts exactly the keys `ServerSettings.api_keys` already accepts.

    No scopes, no expiry, no issuer: this is a flat set of bearer tokens, not
    an OAuth deployment. ``AccessToken.client_id`` is the raw key, which is
    also the ``owner`` value threaded through ``Storage`` and ``JobStore`` —
    the same identity, checked the same way, on every path a caller can reach.
    """

    accepted: frozenset[str]

    async def verify_token(self, token: str) -> AccessToken | None:
        if token not in self.accepted:
            return None
        return AccessToken(token=token, client_id=token, scopes=[])


__all__ = ["ApiKeyVerifier"]
