# ADR 0035 — Remote MCP is a transport bridge over the Cloud Engine server, not a fifth interface

**Status:** Accepted · 2026-08-31

**Amended before acceptance.** A review of the initial proposal below found
five gaps the "Proposed" draft had not closed: job records and idempotency
keys were not scoped to a caller the way uploads were (§4), the ownership
check needed to be one atomic lookup rather than an exists-then-fetch pair
(§4), nothing required TLS for a transport whose whole premise is a
third-party client the operator does not control (new: §6), and the "session
binding" §5 gestured at was not yet built (new: §6, built on a mechanism the
SDK already ships). §4 and §6 below, and the Enforcement section, reflect the
accepted, implemented shape — not the original draft.

**Closes** the five open questions [phases.md](../planning/phases.md#phase-11--remote-mcp-transport-m11)
lists for Phase 11 / M11, and the "Decision first" gate that section holds.

## Context

M10 ([ADR 0027](0027-mcp-is-an-optional-interface-behind-an-extra.md),
[0028](0028-the-mcp-tool-surface-is-the-registry.md),
[0029](0029-the-mcp-policy-boundary.md)) built `docmax.mcp`: a *local* MCP
server, over stdio, for a client that shares a filesystem with the user and can
spawn a process on their machine. ChatGPT's connectors and similar clients can
do neither. `roadmap.md` names exactly what breaks if `docmax.mcp` is simply
mounted on a socket instead of stdio:

- **The tool contract changes.** `docmax.mcp`'s schema takes `inputs` as local
  filesystem paths ([ADR 0028](0028-the-mcp-tool-surface-is-the-registry.md)).
  A remote client has no path to hand the server.
- **The policy boundary changes.**
  [ADR 0029](0029-the-mcp-policy-boundary.md) — roots, offline-by-default,
  consent read from a local `consent.json` — assumes one trusted local caller
  on the same machine as the config file it reads. None of the three concepts
  it checks (a root directory, a one-way local `offline` flag, a human sitting
  at this terminal) means anything to a caller on the network.
- **It may not be a new interface at all.** `docmax.server` is already
  network-reachable ([ADR 0006](0006-reference-server-location.md)) and
  already solved "a caller with no shared filesystem" for the Cloud Engine:
  `POST /v1/uploads` → `file_id` → `POST /v1/tools/{name}` → poll
  `/v1/jobs/{id}` (`docs/cloud-api.md`). Whether M11 reuses that or builds a
  second one is the question this ADR exists to answer.

Two things already exist and are worth being precise about, because the
decision below leans on both:

**`docmax/mcp/schema.py` imports no SDK and names no tool.** It is a pure
function from `ToolSpec` to JSON Schema — registry in, schema out. Nothing
about it is stdio-shaped.

**`docmax/mcp/server.py`'s `DocMaxServer` is stdio-shaped through and
through.** It builds one `EngineRouter` over one resolved `Config`, reads one
local `ConsentStore`, and checks every path against one `Policy` whose roots
default to the process's own working directory. It is a *local, single-caller*
adapter, and every one of those nouns — "one config", "one consent store", "the
working directory" — describes something a network server serving many callers
does not have.

The `.importlinter` `interfaces-are-independent` contract already forbids
`docmax.mcp` and `docmax.server` from importing each other at all, in either
direction, with two narrowly-spelled exceptions that both belong to the CLI's
entry points. That contract is not incidental to this decision — it is a fact
the decision has to fit inside or explicitly amend.

## Decision

**M11 is a route inside `docmax.server`, not a new interface.** It speaks
MCP-over-HTTP and translates `tools/call` into the exact same
`RegistryRunner` / `Storage` / `Job` path that `POST /v1/tools/{tool_name}`
already runs. There is no `docmax.mcp.remote`, no fifth entry in the layers
contract, and no change to `interfaces-are-independent` — `docmax.server` and
`docmax.mcp` still never import each other.

This answers all five open questions from a single choice:

### 1. File reference shape: reuse the existing upload → `file_id` → job model, verbatim

A remote tool call's `inputs` are `file_id`s obtained from the existing
`POST /v1/uploads`. There is no second upload mechanism, no second storage
protocol, and no second idea of what a "reference to something already
uploaded" is — `docmax.server.storage.Storage` already is that idea, and it
already abstracts over the reference backend (`InMemoryStorage` today; a
disk- or object-store-backed one later, per its own docstring). An output is a
`file_id` behind `/v1/outputs/{file_id}`, exactly as `RegistryRunner.start`
already produces for the JSON `file_id` path in `routes/tools.py`.

### 2. Fourth interface vs. transport bridge: **bridge**

`docmax/server/routes/mcp.py` (new) mounts an MCP-speaking endpoint —
the SDK's streamable-HTTP transport, using the same `mcp` package M10 already
depends on — inside the existing FastAPI app, alongside `/v1/uploads`,
`/v1/tools`, `/v1/jobs`. `tools/call` resolves a `ToolSpec` through
`RegistryRunner.resolve` (unchanged — the existing
`EngineNotSupportedError` when a tool has no cloud engine is the correct
answer over this transport too) and drives it through `RegistryRunner.start`
(unchanged). **No second execution path is written.**

**The one piece of M10 this reuses is `schema.py`, and it is promoted, not
imported across the boundary.** Because `docmax.server` may not import
`docmax.mcp` (`interfaces-are-independent`), the registry→JSON-Schema mapping
moves out of `docmax/mcp/` into a new package, `docmax/mcpschema/`, sitting
below both interfaces in the layers contract — on exactly the terms
`docmax.pickers` ([ADR 0019](0019-picker-package-and-rendering.md)) and
`docmax.runners` ([ADR 0023](0023-runners-are-a-package-below-the-interfaces.md))
already sit: wanted by more than one interface, never prints, never exits,
imports `docmax.core` only. `docmax.mcp` re-exports it so `docmax/mcp/server.py`
needs no change beyond its import line. This is a mechanical move, not a
rewrite: `schema.py`'s content is unchanged, because ADR 0028 already built it
SDK-free and tool-agnostic for exactly this kind of reuse.

`docmax/mcp/server.py`'s `DocMaxServer` — the stdio-shaped, single-caller,
roots-and-`ConsentStore` half — is **not** reused and **not** moved. M11 writes
its own small adapter in `docmax/server/routes/mcp.py`, because the two
adapters differ in every place that matters (see §3–5), and forcing one
`DocMaxServer` to serve both would mean branching its behaviour on which
transport called it — the shape CLAUDE.md rule 2 forbids for the CLI and the
same reasoning applies here.

### 3. Auth model: the existing bearer token, nothing new

`docmax/server/routes/mcp.py` depends on `security.require_api_key`, exactly
like every other route. A "remote MCP session" is a caller presenting a valid
key from `DOCMAX_SERVER_API_KEYS` on each request — the streamable-HTTP
transport's own `Mcp-Session-Id` header identifies a *connection*, not a
*principal*; the `Authorization` header still says who is allowed to be here,
the same way it does for `/v1/tools` today. No OAuth, no dynamic client
registration, no second credential to issue or store. `docs/cloud-api.md`'s
existing "An API key is required from day one" stands unchanged for this
transport.

### 4. Whether "roots" mean anything remotely: no — replaced by per-key ownership, everywhere a caller can name an id

There is no filesystem to confine, so there is nothing to port from ADR 0029's
containment check. The equivalent guarantee — *can a caller reach something
another caller created* — is answered at the storage and job layers instead,
and it turned out to have three parts, not one:

- **Uploads.** `Storage.reserve` records which API key reserved a `file_id`,
  and `get` / `put` / `filename` / `discard` require the caller's key to match
  the one on record. The check happens *inside* the same dict lookup that
  finds the record — `_slot(file_id, owner=...)` — not as a separate
  "does it exist" call followed by "is it mine": a `TOCTOU`-shaped gap between
  two calls would otherwise be a second request's whole window. A mismatch
  raises the identical `InputNotFoundError` an unknown id raises, so a caller
  learns nothing about whether the id exists at all — the same shape ADR 0029
  chose for a path outside the roots.
- **Jobs.** `Job` gained the same `owner` field, and `JobStore.get(job_id,
  owner=...)` — reached from `GET /v1/jobs/{id}`, which already required *a*
  key — now requires *the* key that created the job, in the same one-lookup
  shape as storage.
- **Idempotency keys.** This is the one the original draft missed entirely.
  An `Idempotency-Key` is a value the *client* chooses, and
  `find_by_idempotency_key` was keyed only by that value — so a caller
  supplying a key another caller already used, by collision or on purpose,
  was handed back that caller's job, including its output's `file_id`. That is
  the sharpest version of "cross-user data exposure" this design could
  produce, and it existed on `POST /v1/tools/{name}` **before** this ADR, for
  every REST caller — not a gap M11 introduced, but one M11's own review
  surfaced. Fixed by scoping the idempotency index to `(owner, key)`.

**`/v1/outputs/{file_id}` is deliberately exempted, not overlooked.** That
route already takes no API key at all — a real, prior, documented decision:
the file id's own unguessability (96 bits, from `secrets`) is its access
control, because a conforming client (`CloudClient.fetch_output`) downloads
through a bare client, and an authenticated download route would be
unreachable by it. Adding an owner check there would either break that
contract or be unenforceable (there is no caller key to check against).
`Storage.get` therefore takes `owner: str | None`, and `owner=None` — passed
only by `routes/outputs.py` — skips the check by name, not by accident: the
guarantee this route relies on instead is that a `file_id` never reaches the
wrong caller via a job or idempotency lookup, which is exactly what the two
points above close.

This is a handful of fields and comparisons, not a new subsystem, and it
protects every existing REST caller identically to every MCP one — the fix
lives once, below both transports.

### 5. Consent and offline, remotely: moot by construction, not re-derived

ADR 0029 exists because `docmax.mcp` (stdio) can drive the **local** engine on
the user's own machine, so an agent turning `offline` off or granting consent
on the user's behalf would be an agent spending the user's own resources and
credentials without them knowing. `docmax.server` cannot do either of those
things to anyone: `RegistryRunner.resolve` already refuses any tool without a
cloud engine, so **every** call reaching this route is a Cloud Engine call by
construction, on the operator's machine, using capacity the operator already
provisioned by running `DOCMAX_SERVER_API_KEYS=... python -m docmax.server` at
all. There is no local engine reachable through this route to protect against,
and no local `offline` flag or `ConsentStore` in scope — `docmax.server`
already runs with neither, for every existing route, and MCP is not a reason
to add either one now. The operator's act of standing up the server and
issuing a key *is* the consent act, made once, the same shape as
`docmax cloud agree` but performed by the operator rather than by each caller.
`force` is not exposed as an MCP parameter here either, matching
[ADR 0028](0028-the-mcp-tool-surface-is-the-registry.md): every call runs with
`force=False`, so an MCP caller cannot overwrite a `file_id` any more than a
REST one can.

### 6. TLS and session binding: added on review, both close for free

Two questions were not in the original five, and a review of this ADR's first
draft raised both before any of this shipped.

**Does this route require TLS?** `docs/cloud-api.md` already states the rule
for the *client*: "plaintext is permitted for a local endpoint... and refused
everywhere else." Nothing said the same for the server, and this route is the
first one built for a caller the operator may not control end to end — the
project's own `CloudClient` already refuses a plaintext non-local endpoint
itself, but a third-party MCP client (ChatGPT's connectors, or anything else)
has no reason to have made the same choice. `RequireHTTPSMiddleware` applies
the rule at the server for this route: a plaintext connection is refused
unless it is from loopback, checked from the ASGI `scope` directly and placed
**outermost**, ahead of `AuthenticationMiddleware`, so a bearer token is never
evaluated over a channel that should not have carried it in the first place.
Scoped to this route rather than every REST route, because `/v1/tools` et al.
are reached by `docmax`'s own client, which already enforces this on itself —
extending the same check server-side to the rest of the REST surface is a
reasonable follow-up, filed rather than folded in here (see Backlog).

**Can one MCP session be used by two different callers?** Nothing in the
original draft answered this, because nothing in it had built a session
concept yet — REST is stateless per request, and `docmax.mcp` (stdio) has
exactly one caller for the process's whole life. The streamable-HTTP
transport's `StreamableHTTPSessionManager` turns out to already carry the
exact mechanism this needs: it records which verified identity opened each
session and refuses a later request against that session id from a different
one, answering with the same "not found" a made-up session id gets — not a
hint that the session exists but belongs to someone else. Wiring `mcpauth.py`'s
`ApiKeyVerifier` through the SDK's `BearerAuthBackend` / `AuthContextMiddleware`
is what makes that identity the same bearer key already checked everywhere
else, and is the whole implementation — nothing here re-derives session
tracking by hand. This is only true in the SDK's **stateful** mode
(`StreamableHTTPSessionManager(stateless=False)`, its default); stateless mode
creates a fresh, untracked transport per request and carries no such map, so
this route uses stateful mode deliberately, not by leaving a default alone.

## Alternatives considered

**A new peer interface, `docmax.mcp.remote` (or a `docmax.mcp --remote`
flag), reimplementing upload, storage and job tracking against `docmax.core`
directly.** Rejected. It duplicates machinery `docmax.server` already has,
tested, and has already made decisions about (in-process jobs — ADR 0016; the
in-memory store's honesty about its own limits). Two implementations of
"who is allowed to call this and what have they uploaded" is the CLI/router
duplication CLAUDE.md rule 2 forbids, one layer up.

**Port `docmax/mcp/server.py`'s `DocMaxServer` and `Policy` wholesale, and give
`Policy` a remote-shaped configuration mode.** Considered, because it would
mean writing one adapter instead of two. Rejected: every one of `Policy`'s
checks — a default root of the *process's* working directory, an `offline`
flag read from *the* resolved `Config`, a `ConsentStore` keyed to *the*
consent file — assumes exactly one caller and one local identity. Bending it to
also mean "this API key's uploads" and "the operator's cloud engine, which is
always in play" would leave the local, single-caller meaning of every one of
those checks still readable in the code for the stdio path, silently
reinterpreted for the remote one. That is the ADR 0029 "enforce roots inside
`OutputTarget`" mistake one level up: a policy built for one threat model,
generalised until it means something different for a second one nobody
re-examined.

**Let `docmax.server` import `docmax.mcp.schema` directly, and add an ignored
import pair to `interfaces-are-independent`.** Rejected in favour of promoting
the module instead. An ignored-import exception is reserved, by the contract's
own comment, for "the entry point and nothing else" — `docmax.tui`'s and
`docmax.mcp`'s package roots, reached only by `cli.main`. `schema.py` is not an
entry point either side would reach once and stop; it is library code two
interfaces both want to call repeatedly, which is precisely the shape
`docmax.pickers` and `docmax.runners` already exist to hold. Extracting it
follows the precedent the codebase already set twice, rather than starting a
third kind of exception to the same contract.

**A genuinely presigned, third-party object-storage upload flow for M11
specifically**, rather than the server's current self-referential
"presigned" URL. Out of scope for this ADR: `Storage` already abstracts the
backend, `routes/uploads.py`'s docstring already names the swap as future work
independent of any caller, and nothing about MCP-over-HTTP requires it before
`docmax.server`'s existing REST callers get it.

**MCP protocol-level auth (elicitation, OAuth) instead of the existing bearer
token.** Rejected for the same reason ADR 0029 declined MCP elicitation for
M10: unproven, not universally implemented by clients, and the fallback would
have to be the bearer token anyway. Recorded as additive future work, not
blocking.

## Consequences

- **`docmax.mcp` (stdio) and this route are two independent implementations of
  the same protocol, on purpose** — the same argument `cloud-api.md` already
  makes about the client and the server being independent implementations of
  one contract, extended to a second protocol. A bug in one does not agree
  with itself in the other.
- **`docmax/mcpschema/` is a new package**, sitting below `docmax.server` and
  `docmax.mcp` in the layers contract, on the same terms as `docmax.pickers`
  and `docmax.runners` — never prints, never exits, imports `docmax.core`
  only. `docmax/mcp/schema.py` becomes a thin re-export so nothing importing
  it today breaks.
- **`InMemoryStorage._Slot` and `Job` both gain an owner field, and the
  idempotency index is rekeyed to `(owner, key)`.** Small, additive changes to
  server internals — not a new subsystem — and every existing REST caller gets
  the same per-key isolation MCP callers get, for free. The idempotency fix in
  particular closes a real gap that predates this ADR and affected `POST
  /v1/tools/{name}` on its own, found only because this design forced every
  path a `file_id` or `job_id` can travel to be re-read end to end.
- **This route requires TLS except from loopback; the rest of the REST API
  does not (yet).** `docmax`'s own `CloudClient` already refuses plaintext on
  itself, so the gap is real but only reachable by a client that did not make
  the same choice — named here and filed as backlog rather than expanded into
  this change.
- **A session belongs to the credential that opened it, enforced by the SDK's
  session manager, not by code this project wrote and must keep correct by
  hand.** The cost is running in stateful mode, which holds one transport per
  active session for the manager's lifetime — bounded by however many
  concurrent sessions a deployment actually has, not by anything this ADR
  introduces.
- **Only tools with a cloud engine are ever offered over this route.** A
  client listing tools over remote MCP sees a strict subset of what a local
  `docmax mcp` client sees, and `docs/cloud-api.md`'s `/v1/capabilities`
  answers the same question for REST already — this is not a new asymmetry,
  it is the existing one surfacing on a second transport.
- **No new consent or offline model was designed, on purpose.** This is the
  main thing choosing "bridge" over "fourth interface" buys: the hardest open
  question in `phases.md` turned out to already be answered by
  `docmax.server`'s existing shape, once the transport stopped being conflated
  with the trust boundary.
- **A TOCTOU-shaped question remains: can request A read request B's
  `file_id` between the ownership check and the read.** Named rather than
  fixed for the same reason ADR 0029 left its own symlink-race gap
  named — real, small under this reference backend's in-memory, single-process
  model, and worth revisiting if a future storage backend introduces a real
  window (a network call between the check and the read, for instance).

## Enforcement

- `.importlinter`: `docmax.mcpschema` joined the `layers` contract below
  `docmax.server` and `docmax.mcp`, and below `docmax.core` in
  `core-is-standalone`. `interfaces-are-independent` is unchanged, holding
  that `docmax.server` and `docmax.mcp` still have no import edge to each
  other in either direction (`lint-imports`, run over the finished change,
  keeps this an assertion rather than a claim).
- `tests/unit/test_m11_mcp.py::test_a_caller_cannot_read_a_file_id_it_did_not_upload`
  and `::test_a_caller_cannot_fill_in_bytes_for_a_file_id_it_did_not_reserve` —
  §4's storage half.
- `::test_a_caller_cannot_poll_a_job_it_did_not_create` — §4's job half.
- `::test_an_idempotency_key_collision_does_not_hand_over_someone_elses_job` —
  §4's idempotency half, and the one gap that predates this ADR.
- `::test_outputs_stay_reachable_with_no_key_by_design` — the deliberate
  exemption in §4 did not regress into an accidental auth requirement.
- `::test_mcp_tool_list_matches_the_cloud_capable_registry` and
  `::test_no_mcp_tool_offers_force` — the same restrictions
  `test_m10_schema.py` and `RegistryRunner.resolve` already hold for the local
  server and for REST, asserted for the remote MCP surface too.
- `::test_a_session_cannot_be_reused_by_a_different_key` — §6's session
  binding, driven through a real handshake and a real session id, not a call
  to a helper function.
- `::test_plaintext_from_a_non_loopback_address_is_refused`,
  `::test_plaintext_from_loopback_is_allowed_through_to_authentication` and
  `::test_mcp_route_requires_a_valid_key` — §6's TLS requirement, including
  that the loopback exemption is an exemption from TLS specifically and not a
  bypass of authentication.
- `::test_a_tool_call_over_mcp_runs_through_the_same_registry_runner` — a
  full upload → call → succeed round trip through the real `RegistryRunner`,
  not a mock of it.
- `::test_the_mcp_route_never_references_consentstore` — an AST scan over
  `routes/mcp.py` and `mcpauth.py`, the direct analogue of
  `test_m10_policy.py::test_the_server_never_records_consent`.
- `tests/paths.py::LIBRARY_PACKAGES` includes `mcpschema`, enrolling it in the
  no-direct-writes and no-sys-exit hygiene suites on the same terms as
  `pickers` and `runners`.
