# ADR 0037 — Tokens are durable, hashed, and belong to a user; the env-var allowlist becomes the bootstrap path, not the identity model

**Status:** Accepted · 2026-09-02

**Closes** the "Per-key tool authorization, once a real per-user identity
exists" item [backlog.md](../planning/backlog.md#important) has been carrying
since M11, by building the identity that item was waiting on.

## Context

`docmax.server` authenticates exactly one way today, for both interfaces it
has: `security.require_api_key` (REST) and `ApiKeyVerifier` (`/v1/mcp`,
[ADR 0035](0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md))
both check a presented bearer token against `ServerSettings.api_keys` — a
`frozenset[str]` parsed once, at process start, from one environment variable
(`DOCMAX_SERVER_API_KEYS=key1,key2,...`). `AccessToken.client_id` and the
`owner` threaded through `Storage`/`JobStore` are, both of them, *the raw
token string*. There is no user underneath it — a token is not issued to
anyone, it is simply a string an operator typed into an env var and can be
compared against.

That was the right amount of mechanism for a reference server proving out the
execution model ([ADR 0016](0016-jobs-run-in-process.md)) and the MCP
transport bridge ([ADR 0035](0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md)).
It stops being enough the moment a deployment is meant to serve more than its
own operator indefinitely:

- **Revoking access means editing an env var and restarting the process.**
  There is no way to invalidate one caller's access without either killing
  every caller's session or redeploying.
- **A token is not attributable to anyone.** `owner` is the token string
  itself, so "who is calling" and "which credential is calling" are the same
  question, which stops being true the moment one person is meant to hold
  more than one token (a script, and a personal one) or one token might be
  handed to a teammate.
- **A leaked backup or a `ps` on the wrong process is a leaked, permanent,
  unrevocable credential.** Nothing about the current model distinguishes
  "this token is live" from "this string appears in `api_keys`."

This is a different kind of gap from the ones ADR 0016 named and left open —
that ADR rejected persisting *jobs* to SQLite as "speculative... nothing needs
a job to survive a restart yet." Identity is not speculative: a token that
does not survive a restart cannot be handed to anyone as a credential worth
having, which is exactly the requirement this ADR exists to state.

Rate limiting, per-key tool scopes, and automatic reaping are named in
[ADR 0035](0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md)
and [backlog.md](../planning/backlog.md#important) as separate, deferred gaps.
This ADR does not close them — it builds the one thing they all assume
exists: a real caller identity to hang a scope, a quota, or a revocation on.

## Decision

### 1. A token belongs to a user; `owner` becomes the user's id, not the token string

A **user** is a row with an id, an optional label (`"ci"`, `"jane"` — for the
operator's own bookkeeping, never validated or unique), a `created_at`, and a
`disabled_at` that is normally null. A **token** belongs to exactly one user,
and a user may hold more than one token — a script and a person are different
callers even when the same account is billed for both, and revoking the
script's token must not touch the person's.

Every place that currently receives `owner: str` (`Storage`, `JobStore`)
keeps receiving `owner: str` — but the value it receives changes from *the
raw token* to *the user id the token resolved to*. This is a one-line change
at the two auth boundaries (`require_api_key`, `ApiKeyVerifier.verify_token`)
and no change at all to `storage.py` or `jobs.py`, because both already treat
`owner` as an opaque identity string, checked the same way, in the same
lookup that finds the record — the property [ADR 0035](0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md)
built and this ADR reuses rather than re-litigates.

### 2. Tokens are random, prefixed, and stored hashed

A newly issued token is `dmx_live_` followed by 32 bytes of
`secrets.token_hex`, matching the format `cloud-api.md`'s own example
(`Authorization: Bearer dmx_live_...`) already documents — this ADR makes
that example literal rather than illustrative.

**Only `sha256(token)` is ever written to disk.** The raw value is returned
exactly once, in the response to the command that created it, and is not
retrievable afterward — the same guarantee a lost password gets: reset, don't
recover. Verifying a presented token hashes it and looks up the hash; nothing
about the check needs the raw value to be stored anywhere.

### 3. `SqliteIdentityStore`, a new module, a new file, a new self-exemption

```python
class IdentityStore(Protocol):
    def create_user(self, *, label: str | None = None) -> str: ...
    def create_token(self, *, user_id: str, label: str | None = None) -> str: ...
    def verify(self, token: str) -> str | None:            # -> user_id
    def revoke(self, token_id: str) -> None: ...
    def list_tokens(self, user_id: str) -> list[TokenInfo]: ...
    def list_users(self) -> list[UserInfo]: ...
```

`revoke` takes no `user_id`: unlike `Storage`/`JobStore`, which distinguish
between many *different* HTTP callers by their `owner`, every caller of this
protocol's administration methods is the same person — whoever holds shell
access to the file, per §4. There is no second party to check ownership
against.

mirrors `Storage`'s shape in `storage.py`: one lookup every method goes
through. The reference implementation, `SqliteIdentityStore`, is a single
file (`identity.db`, path set by the operator via
`DOCMAX_SERVER_IDENTITY_DB`) using Python's stdlib `sqlite3` — no new
dependency, the same reasoning [ADR 0014](0014-api-key-storage.md) already
gave for rejecting `keyring`: this is a self-hosted, single-operator-oriented
product, not one that should grow a database server to answer "is this token
still good."

**This turned out to need no entry in `test_no_direct_writes.py`'s `EXEMPT`
set at all — a narrower and more accurate outcome than a fourth
self-exemption.** That test's AST scan looks for specific patterns:
`open(..., "w")`, `.write_text()`/`.write_bytes()`, and a short list of
library calls (`cv2.imwrite`, `shutil.move`/`copy`). A `sqlite3.connect(...)`
and `conn.execute(...)` match none of them, so the scan simply never fires on
this module — there is nothing to exempt it *from*. The reasoning that would
have justified an exemption is recorded instead in `identity.py`'s own module
docstring and in that test's docstring, so the omission reads as considered
rather than as a gap nobody noticed: `core/atomic.py`'s guarantee — stage
beside the destination, validate, then `os.replace` — is built for *document*
writes, a whole file replaced in one step. A SQLite database earns the
identical guarantee (no reader ever observes a partial write) a different
way, through its own per-statement transaction, and routing an `INSERT`
through `os.replace()` would not make it safer — it would replace a live,
possibly-concurrently-read database file on every write.

### 4. Issuance is a standalone script run by the operator, not an HTTP endpoint

```bash
python -m docmax.server.identity_cli create-user --label jane
python -m docmax.server.identity_cli create-token --user <id> --label laptop
python -m docmax.server.identity_cli revoke <token-id>
python -m docmax.server.identity_cli list --user <id>
```

**This is not a `docmax server identity` subcommand of the base CLI, which
is what this section originally proposed before implementation found the
reason that shape cannot work.** `docmax.server` is deliberately excluded
from the wheel ([ADR 0006](0006-reference-server-location.md)) and
`docmax.cli` — the package the base install ships — may never import it;
`tests/hygiene/test_wheel_excludes_server.py` enforces exactly that and
catches a `cli/` module importing `docmax.server.identity` as a build
failure. So this lives at `server/identity_cli.py` instead, its own
`argparse`-based entry point invoked the same way the server itself already
is — `python -m docmax.server...`, from a checkout, with the `server` extra
installed, by whoever has shell access to the machine. That is the same
person who already holds `DOCMAX_SERVER_API_KEYS` or the deploy credentials,
so this adds no new secret to protect and no admin HTTP surface to bootstrap
trust for.

Kept in its own module rather than folded into `__main__.py`, for the reason
`__main__.py` already gives for staying separate from `app.py`: importing it
never binds a port, and running the server never parses `identity`
subcommands it does not need. It calls no `sys.exit` — `test_no_sys_exit.py`
scans everything under `server/`, this script included, with no carve-out
for an entry point — so a failure is an uncaught `DocMaxError`, printed with
its message and remedy before it is re-raised, and Python's own handling of
an uncaught exception supplies the non-zero exit code.

Nothing here goes through `EngineRouter`, and nothing about it is reachable
through the tool registry, the TUI, or MCP: an agent that can invoke tools
must never be the thing that can also mint credentials for other callers.

### 5. The env var becomes the bootstrap path, additive, not replaced

`DOCMAX_SERVER_API_KEYS` keeps working exactly as it does today — this is
what keeps every existing test that constructs `ServerSettings(api_keys=...)`
passing unchanged, and what keeps `DOCMAX_SERVER_API_KEYS=dev-key python -m
docmax.server` a true one-line quickstart with zero setup. A key from that
set resolves to a **degenerate user whose id is the key itself** — which is
exactly what `owner` already equals today, so nothing about the *existing*
behavior changes; it becomes the trivial case of the general model rather
than a separate code path.

Both backends are consulted at auth time: the static set first (an in-memory
comparison, no I/O), then `IdentityStore.verify()` if an `identity.db` is
configured (`DOCMAX_SERVER_IDENTITY_DB=<path>`, unset by default). A
deployment can run with only the env var forever, add the durable store
later without a flag day, or eventually stop setting the env var once every
real caller holds an issued token.

## Alternatives considered

**Self-contained tokens (JWTs).** Rejected: revocation before expiry needs
either a short TTL with refresh (a protocol this product has no other reason
to speak) or a denylist — and a denylist checked on every request is exactly
the store this ADR builds anyway, so a JWT buys nothing here and adds a
signing-key management problem this deployment shape does not have a good
place to put.

**Full OAuth2 / OIDC**, an authorization server with a token endpoint and
dynamic client registration. Rejected for the same reason
[ADR 0035](0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md)
already declined it for MCP specifically: there is no third-party app or
multi-tenant story yet that needs an issuer distinct from the operator
themself. If DocMax ever needs to let *other people's* applications request
access on a user's behalf, this is revisited then, deliberately, not
smuggled in now under a bigger word than the problem needs.

**A managed external database (Postgres, etc.).** Rejected: adds an
infrastructure dependency to a product whose whole reference-server pitch
(ADR 0006, ADR 0016) is "runs from a checkout, no broker, no compose file."
SQLite is stdlib and the file is the whole deployment artifact to back up.

**Storing tokens in plaintext, like [ADR 0014](0014-api-key-storage.md)
chose for the *client*-held key.** Not the same threat model: ADR 0014's key
is one credential, on one user's own machine, protecting nothing but that
user's own documents. This store holds every caller's live credential for a
shared deployment — a leaked backup here is every caller's access at once,
which is the difference that justifies hashing where ADR 0014 reasoned
hashing wasn't worth it.

**An HTTP admin endpoint for issuing tokens**, so a deployment could be
managed remotely. Rejected for now: it re-creates the bootstrapping problem
one level up — something has to authenticate *that* endpoint — and the
answer would be "a master token," which is one more static secret, not
fewer. A CLI command run by whoever already has shell access sidesteps the
problem rather than solving a harder version of it. Revisited if DocMax ever
targets an operator who provisions the server but does not have a shell on
it.

## Consequences

**What it buys.** One identity model, still, for both HTTP and MCP — this
ADR generalizes the existing check point, it does not add a second one.
Revocation without a restart. A leaked `identity.db` backup does not hand
over live credentials. "Which user did this" becomes an answerable question
for the first time, which is what per-key tool authorization, rate limiting,
and audit logging — every one of them still separately deferred — need in
order to be buildable at all.

**What it costs, stated plainly:**

- **A new file to operate, with the same permissions caveat ADR 0014 already
  named for `config.toml`:** `0600` on POSIX, whatever the Windows profile
  ACL gives otherwise, not tightened.
- **No self-serve.** Issuing a token requires shell access to the server.
  Fine for the single-operator deployments this product targets today; a
  real blocker if DocMax ever wants a signup flow.
- **Still no scopes, no rate limiting, no per-key tool authorization.** This
  ADR makes all three *implementable* — they now have a user to attach to —
  but does not implement any of them. They stay exactly where
  backlog.md already has them, Important and unscheduled.
- **A second thing to reconcile during disaster recovery.** A deployment now
  has two pieces of durable-ish state with different lifecycles: `identity.db`
  (this ADR, meant to survive a restart) and the in-memory job store
  ([ADR 0016](0016-jobs-run-in-process.md), meant not to). Restoring one from
  backup without the other is a coherent, supported state — a restored
  identity store with no jobs in flight — so this is not a new failure mode,
  but it is a new thing to explain to an operator.

## Enforcement

- `tests/unit/test_identity.py` asserts `IdentityStore.verify()` returns the
  identical `None` for an unknown token, a malformed one, and a revoked one
  — no distinguishing signal, the same pattern `storage.py::_slot` already
  uses for ownership mismatches — and that revoking an unknown or
  already-revoked token id raises `IdentityNotFoundError` either way.
- The same file asserts the raw token value is never retrievable after
  creation: not from `list_tokens` (`TokenInfo` carries no field that could
  hold it), and not from the sqlite file's bytes on disk.
- The same file asserts the database is a single file at rest — no `-wal`
  or `-shm` sibling — holding the "one file to back up" claim in the
  Alternatives section below to account, and that the store persists across
  a reconnect (a fresh `SqliteIdentityStore` over the same path sees what an
  earlier instance wrote).
- `tests/unit/test_m11_mcp.py` asserts a key accepted through
  `DOCMAX_SERVER_API_KEYS` and a token issued through `identity.db` produce
  ownership-check behavior that is indistinguishable from the caller's side
  — same error shape for an unknown/revoked token, same isolation between
  two different *users'* uploads — and, the concrete new capability this ADR
  adds, that two different *tokens* for the same user share ownership of an
  upload where two tokens for different users do not.
- `test_identity.py::test_token_administration_is_unreachable_from_tool_execution`
  AST-scans every library package for a call to `create_user`,
  `create_token`, `revoke`, `list_tokens`, or `list_users` outside
  `identity.py` and `identity_cli.py` themselves — an agent driving DocMax
  through MCP must never be able to reach token issuance, mirroring the
  reasoning `test_the_mcp_route_never_references_consentstore` already holds
  for consent.
- POSIX file permissions on `identity.db` (`0600`) are asserted directly,
  skipped on Windows with that stated as the reason — the same pattern
  ADR 0014 already established for `config.toml`.
- Not yet enforced by anything, and named rather than assumed: that every
  future auth-adjacent code path resolves identity through this module
  rather than reading `ServerSettings.api_keys` directly, and reaping of
  revoked tokens (§ Consequences).

## Implementation impact

- **Code (new):** `server/identity.py` — the `IdentityStore` protocol and
  `SqliteIdentityStore`. `server/identity_cli.py` — the standalone
  administration script, per §4.
- **Code (changed):** `core/errors.py` gains `IdentityError` /
  `IdentityNotFoundError`. `security.require_api_key` and
  `mcpauth.ApiKeyVerifier.verify_token` resolve identity through both
  backends per §5, and return a user id rather than the raw token as
  `owner`/`client_id`. `ServerSettings` gains `identity_db_path: Path | None`
  from `DOCMAX_SERVER_IDENTITY_DB`. `routes/mcp.py::build_mcp_asgi_app` and
  `app.py::create_app` thread an optional `identity` parameter through. No
  change to `storage.py`, `jobs.py`, or any route handler's body — `owner`
  stays an opaque string everywhere it already is one.
- **Config:** `pyproject.toml`'s `ruff` per-file-ignores gains
  `server/identity_cli.py` for `T20` (print), on the same reasoning already
  given for `cli/**` — this script is a terminal-facing entry point forced
  to live under `server/` rather than `cli/`, not library code.
- **Docs:** `cloud-api.md`'s Auth section, which already showed
  `dmx_live_...` as an example, describes both backends; a new
  `implementation/identity.md` alongside the pattern
  `implementation/mcp.md` and `implementation/runners.md` already set.
- **Not in scope, named so it isn't assumed solved by this:** rate limiting,
  per-key tool scopes, automatic reaping of jobs/storage/revoked tokens, an
  HTTP admin surface. All remain exactly where `backlog.md` already lists
  them.
