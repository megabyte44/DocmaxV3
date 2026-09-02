# Server identity

ADR 0037. `docmax.server` accepts a caller in two ways at once: a static
allowlist (unchanged since M6) and a durable, per-user token store. Both
resolve to the same `owner` value, checked the same way, on every path a
caller can reach — REST and `/v1/mcp` alike.

The decision is in [ADR 0037](../adr/0037-server-token-identity.md). This
file describes what exists.

---

## Two backends, one identity model

```
Authorization: Bearer <token>
  → security.require_api_key (REST) / mcpauth.ApiKeyVerifier (MCP)
      1. token in ServerSettings.api_keys?  → owner = the key itself
      2. app.state.identity.verify(token)?  → owner = the user id it belongs to
      3. neither                            → 401 / MCP auth failure
```

Both auth boundaries call the identical two steps in the identical order —
`security.py` and `mcpauth.py` do not share code, because MCP's `TokenVerifier`
protocol and FastAPI's dependency shape are different enough that sharing a
function would cost more than it saved, but they share the *decision*, and a
test in `test_m11_mcp.py` exercises both.

A key from the static set is the trivial case of the general model, not a
special case in the code: it resolves to a caller whose id is the key string
itself, which is exactly what `owner` already equaled before `identity`
existed.

---

## `IdentityStore`

```python
class IdentityStore(Protocol):
    def create_user(self, *, label: str | None = None) -> str: ...
    def create_token(self, *, user_id: str, label: str | None = None) -> str: ...
    def verify(self, token: str) -> str | None: ...
    def revoke(self, token_id: str) -> None: ...
    def list_tokens(self, user_id: str) -> list[TokenInfo]: ...
    def list_users(self) -> list[UserInfo]: ...
```

`verify()` is the one method a request path calls. Everything else is
administration — see below.

### `SqliteIdentityStore`, the reference implementation

One file, stdlib `sqlite3`, no new dependency — the same reasoning
[ADR 0014](../adr/0014-api-key-storage.md) already gave for rejecting
`keyring`. Deliberately **not** WAL: the default rollback-journal mode leaves
exactly one file at rest (`identity.db`, no `-wal`/`-shm` siblings), which is
what lets an operator back this store up by copying it — the same property
that made SQLite win over an external database server in the ADR's
alternatives.

A single connection, `check_same_thread=False`, guarded by one `threading.Lock`:
`require_api_key` is a synchronous FastAPI dependency, which Starlette runs in
a thread pool, so more than one thread can reach the store per process.

### Tokens

`dmx_live_` followed by 32 bytes of `secrets.token_hex` — the format
`cloud-api.md`'s Auth section has shown as an example since before this
module existed. Only `sha256(token)` is ever written to disk; the raw value
is returned once, from `create_token`, and is not retrievable afterward —
`test_identity.py` asserts this against both the API (`TokenInfo` carries no
raw-value field) and the database file itself (the raw bytes are absent from
it).

`verify()` checks the token's prefix before touching the database — a format
check, not a lookup, so it costs nothing to answer before the query and
reveals nothing about any particular stored token, the same reasoning
`storage.py::InMemoryStorage._slot` applies to a `file_id`.

### Users

A user is a row with an id, an optional label (never validated or unique —
an operator's own bookkeeping), and `created_at`. A user may hold more than
one token: a script and a person are different callers even when the same
account is billed for both, and revoking one must not touch the other. Two
tokens for the same user resolve to the identical `owner`, which is the
concrete new capability this store adds — `test_m11_mcp.py`'s
`test_two_tokens_for_the_same_user_share_upload_ownership` is the test that
would have been impossible to write before this module existed.

### Revocation returns the identical signal as "never existed"

`verify()` returns `None` for an unknown token, a malformed one, and a
revoked one — no distinguishing signal, the same shape ADR 0029 already
chose for a path outside an MCP client's roots, and the same shape
`storage.py` chose for an ownership mismatch. `revoke()` on an unknown or
already-revoked token id raises `IdentityNotFoundError`, likewise without
saying which of the two it was.

---

## Administration: `python -m docmax.server.identity_cli`

ADR 0037 originally sketched this as a `docmax server identity` subcommand
of the base CLI. Implementation found the reason that does not work:
`docmax.server` is excluded from the wheel
([ADR 0006](../adr/0006-reference-server-location.md)) and `docmax.cli` — the
package the base install ships — may never import it
(`tests/hygiene/test_wheel_excludes_server.py`). So this is its own script,
invoked the way the server itself is: from a checkout, with the `server`
extra installed.

```bash
python -m docmax.server.identity_cli create-user --label jane
python -m docmax.server.identity_cli create-token --user u_... --label laptop
python -m docmax.server.identity_cli list --user u_...
python -m docmax.server.identity_cli revoke t_...
```

Reads `DOCMAX_SERVER_IDENTITY_DB` directly — the same variable the running
server resolves — rather than taking a path argument, because the store is a
property of *the deployment*, not a file a single invocation should be able
to point at by accident.

No `sys.exit` anywhere in this module: `tests/hygiene/test_no_sys_exit.py`
scans every file under `server/`, this one included, on the same reasoning
`paths.py` gives for the package as a whole — nothing in it gets to decide
unilaterally that the process should die. A failure is an uncaught
`DocMaxError`: its message and remedy print to stderr, then it is re-raised,
so Python's own handling of an uncaught exception supplies the non-zero exit
code this script never asks for by name.

---

## What administers, and what may never reach it

Run by whoever already has shell access to the deployment — the same person
who already holds `DOCMAX_SERVER_API_KEYS` or the deploy credentials, so this
adds no new secret to protect and no admin HTTP surface to bootstrap trust
for. **Nothing reachable through a tool call or an MCP client may reach
issuance or revocation** — an agent driving DocMax through MCP must never be
the thing that can also mint or revoke credentials for other callers, the
same reasoning ADR 0029 applies to consent.

`test_identity.py::test_token_administration_is_unreachable_from_tool_execution`
holds this structurally: it AST-scans every library package for a call to
`create_user`, `create_token`, `revoke`, `list_tokens`, or `list_users`
outside `identity.py` and `identity_cli.py` themselves, mirroring
`test_m11_mcp.py::test_the_mcp_route_never_references_consentstore`.

---

## What this does not add

Named so it isn't assumed solved by this:

- **No scopes or per-key tool authorization.** Every accepted token — static
  or issued — can run every cloud-capable tool. This module builds the user
  identity that capability would attach to; it does not add the capability.
- **No rate limiting or quotas.** A valid token can call any route as often
  as it likes.
- **No automatic reaping.** `identity.db` only grows; nothing deletes an old,
  revoked token's row.
- **No self-serve.** Issuing a token requires shell access to the machine
  running the deployment. There is no signup flow and no HTTP admin surface.
- **No OAuth.** `mcpauth.ApiKeyVerifier` implements the SDK's
  `TokenVerifier` shape because that is what the transport needs to plug
  into, not because there is an issuer, a token endpoint, or dynamic client
  registration behind it. See `mcpauth.py`'s own module docstring.

All five are named in [ADR 0037](../adr/0037-server-token-identity.md)'s
Consequences section and in `backlog.md`, unscheduled.
