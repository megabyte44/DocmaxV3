# Implementation phases

## How this relates to the roadmap

There are two views of the work, and they are not competing plans:

- **Milestones (M0–M10)** in [roadmap.md](roadmap.md) are the *product* view —
  what a user can do, in the order it ships. They are public, in the README, and
  phrased as capabilities.
- **Phases** here are the *engineering* view — the order the foundations have to
  be built. Almost all of them deliver M1, after which milestones M2 onward are
  feature increments that reuse the same foundation.

One is "what ships"; the other is "what has to exist first". When they appear to
disagree, the milestone is the commitment and the phase is the route.

Status values: `not started` · `in progress` · `complete` · `blocked`.

---

## Phase 0 — Repository baseline

**Goal** A committed foundation: layering, error model, CI, and the structural
guarantees that make v2's failure modes unreachable.

**Status** `complete` — commit `4fc92f2`, milestone M0.

**Delivered** typed error hierarchy · branding single-source · CLI shell and
`doctor` · four hygiene tests · import-linter contracts · CI across 3 OSes × 3
Python versions plus a golden and an open-core job · ADRs 0001–0005.

---

## Phase 1 — Architecture mapping and documentation system

**Goal** Make the architecture and the plan legible in the repository, and make
documentation drift visible rather than silent.

**Status** `complete` — 2026-08-15.

**Delivered**
- `docs/architecture/` — `overview.md` (promoted from the old top-level
  `architecture.md`),
  `layers.md`, `dependencies.md`
- `docs/planning/` — `roadmap.md`, `phases.md`, `current-status.md`, `backlog.md`
- `docs/adr/README.md` — index and format, with a mandatory Enforcement section
- `dependencies.md` separates rules CI enforces from rules it does not

**Definition of done** ✅ every architectural rule in the docs is either enforced
or explicitly listed as unenforced; no document describes behaviour the code does
not have.

---

## Phase 2 — Core contracts

**Goal** The types and protocols every other layer speaks, with no third-party
dependency.

**Status** `complete` — 2026-08-15. Full toolchain green locally; CI matrix
still outstanding, see the Definition of done.

**Depends on** Phase 0.

**Why it was next** It unblocks every other phase, and it is pure standard
library — which, during the period when PyPI was unreachable from the
development machine, made it the only substantial work that could be verified
locally at all.

**Delivered**

| # | Module | Contracts |
|---|---|---|
| 1 | `core/models.py` | `DocumentRef`, `OutputTarget`, `ToolResult`, `Engine` |
| 2 | `core/protocols.py` | `ProgressSink`, `NullProgress`, `EngineStrategy`, `Validator` |
| 3 | `core/atomic.py` | `atomic_write`, `atomic_path`, `atomic_dir` |
| 4 | `core/cancellation.py` | `CancellationToken`, `NEVER_CANCELLED` |

`errors.py` and `branding.py` were reviewed and needed **no change** —
`errors.py` already carried every error type these modules raise.

Two things were deliberately left out as speculative at this phase: a
`JobStatus` enum (belongs with the cloud client, Phase 7) and storage/cloud
protocols (Phase 8). A protocol with no second implementation and no cross-layer
caller is indirection, not architecture.

**Tasks**
- [x] domain models, with `resolve()` owning every destination check
- [x] boundary protocols, structural rather than inherited
- [x] atomic write / path / dir
- [x] cancellation with cooperative checks, callbacks, and accumulating deadlines
- [x] unit tests per module, written against the failure each prevents
- [x] `implementation/core.md`
- [x] `test_no_direct_writes.py` no longer exempts a file that does not exist

**Definition of done**
- [x] ADR 0003's guarantees hold under test: a crash mid-write leaves the
      destination untouched, an input can never be an output, a cancelled
      multi-file run leaves no partial directory
- [x] core imports nothing heavy — verified by subprocess probe per module
- [x] documentation updated and consistent with the code
- [x] full toolchain passes locally — `pytest` (114 passed, 3 skipped),
      `ruff`, `ruff format`, `mypy --strict`, `lint-imports` (3 contracts kept)
- [ ] **verified on all three platforms** — still outstanding. Local runs are
      Windows / CPython 3.14 only, and 3.14 is not in the supported matrix.
      Needs CI on Linux and macOS across 3.11–3.13.

**Findings worth keeping**

- **The in-place check survives a case-insensitive filesystem.** On Windows and
  default macOS, `-o DOC.PDF` against an input of `doc.pdf` is the same file.
  It works because both `DocumentRef.from_path` and `OutputTarget.resolve` call
  `Path.resolve()`, which returns the name as the filesystem stores it. There is
  now a test that probes the filesystem and asserts accordingly, rather than
  assuming from the platform.
- **`issubclass` cannot demonstrate the absence of inheritance** for a
  method-only runtime protocol — it answers `True`. `test_protocols.py` checks
  the MRO instead.

---

## Phase 3 — Configuration

**Goal** One configuration strategy with one precedence chain, and a consent
record that fails closed.

**Status** `complete` — 2026-08-16. Full toolchain green; CI matrix outstanding.

**Depends on** Phase 2.

**Decision first** [ADR 0008](../adr/0008-consent-record.md) settled where the
consent record lives, what invalidates it, and how it is versioned — before any
code, because a consent mechanism designed around its implementation is a
mechanism designed backwards.

**Delivered**

| Module | Contracts |
|---|---|
| `core/config.py` | `Config`, `load()`, `config_dir/file()`, `consent_file()` |
| `core/consent.py` | `ConsentGrant`, `ConsentStore`, `CONSENT_TERMS_VERSION` |

**Precedence**, lowest to highest:

```
defaults  →  config file (TOML)  →  environment  →  runtime override
```

`load()` applies the first three; `Config.with_overrides()` applies the fourth,
because only the interface knows what the user typed. Both the path and the
environment are injectable, so no test reads a real home directory.

**Tasks**
- [x] ADR 0008 — consent location, invalidation, versioning
- [x] `core/config.py` with the full precedence chain
- [x] `offline`, one-way: an override may turn it on, never off
- [x] per-tool engine preference, merging file and environment rather than
      replacing
- [x] validation at load — unknown keys refused, TLS required except localhost,
      closed set of booleans
- [x] `core/consent.py`, writing through `core/atomic.py`
- [x] 77 unit tests
- [x] `implementation/config.md`; `cloud-api.md` points at the terms constant

**Definition of done**
- [x] precedence tested at every level, including the merge behaviour
- [x] `offline = true` survives an explicit override
- [x] a misspelled key is an error, not a silent no-op
- [x] consent fails closed on corrupt, unreadable, and future-schema records
- [x] `pytest` 197 passed / 3 skipped · `ruff` · `ruff format` · `mypy --strict`
      · `lint-imports` 3 contracts kept
- [ ] **CI matrix** — Windows / CPython 3.14 locally only, as for Phase 2

**Findings worth keeping**

- **`offline` had to be made one-way.** An override that could clear it would
  make the flag decoration: it exists for the person whose policy says documents
  do not leave the building, and `--engine cloud` must not defeat that.
- **An unrecognised boolean must raise, not default.** `DOCMAX_OFFLINE=maybe`
  read as false would send documents. The accepted set is closed and anything
  else is an error.
- **Consent is scoped to the endpoint**, which is what makes a re-pointed
  endpoint re-ask automatically rather than depending on the user remembering.

**Deliberately not here** — the router (Phase 5) is what applies precedence
against availability and consent, and the architecture's stronger claim that *no
path reaches `cloud_client` without a consent check* is its to enforce. Phase 3
provides the mechanism, not yet the guarantee.

---

## Phase 4 — Tool registry

**Goal** Lazy discovery, per [ADR 0002](../adr/0002-registry-mechanism.md).

**Status** `complete` — arrived on `main` via PR #1 rather than through this
phase plan. See [ADR 0009](../adr/0009-main-is-the-base.md).

**Delivered** `core/registry.py` — `ToolSpec`, `Param`, `register`, a
`find_spec` directory scan of `tools/*/tool.py`, `importlib.metadata` entry
points for third-party tools, and strategy loading on demand.

**Definition of done** ✅ `test_building_the_registry_pulls_in_nothing_heavy` is
enabled and passing: building the full registry imports no tool implementation
and no heavy dependency.

**Still open** `get_tool`'s remedy names no command ("Run `tools` to see
everything available"). Cosmetic; fold into Phase 5 or the CLI work.

---

## Phase 5 — Engine router

**Goal** The single orchestration path both the CLI and the server call.

**Status** `complete` — 2026-08-16. **Completes Core.**

**Delivered** `core/router.py` — `EngineRouter` and `Routing`. Both callers
already existed: the CLI, and `server/execution.py`, whose `start()` is stubbed
waiting for exactly this.

**Two small additions it required**
- `Config.default_engine` (settable as top-level `engine = "..."` or
  `DOCMAX_ENGINE`). Without it, precedence rung 3 — "global default" — did not
  exist. `Config.engine_for()` consequently returns `Engine` rather than
  `Engine | None`, so the fallback is not each caller's to remember.
- `protocols.NULL_PROGRESS`, mirroring `NEVER_CANCELLED`, so `run()` can require
  both arguments while costing an indifferent caller nothing.

**Definition of done**
- [x] resolution follows the documented ladder, and every rung is tested
- [x] `offline` beats an explicit `--engine cloud`, and is checked before consent
      so a policy never surfaces as a prompt
- [x] no route to the cloud bypasses the consent gate — including the automatic
      fallback, which is the branch that would otherwise upload quietly
- [x] progress and cancellation reach the strategy; the null constants are
      substituted when a caller has neither
- [x] a typed error propagates unchanged; anything else becomes `InternalError`
      with the original kept as `__cause__`; `KeyboardInterrupt` is not swallowed
- [x] 39 router tests, all against fakes — a router test needing pypdf would be
      evidence of a design failure

**Why it is the linchpin** Everything cross-cutting lives here so that no tool
and no interface implements it twice: engine resolution, consent, `--dry-run`,
cancellation, timing, and wrapping any escaping non-`DocMaxError` in
`InternalError` so no UI ever renders a traceback. Without it, each interface
grows its own orchestration — the duplication the whole architecture exists to
prevent.

**Resolution precedence** explicit argument → per-tool config → global default →
`auto`. `auto`: local available → local; local missing → cloud **only with
recorded consent**, else `ConsentRequiredError`; no network → local; neither →
`NoEngineAvailableError` naming both reasons.

**Definition of done** a test asserts no path reaches `cloud_client` without
passing a consent check.

---

## Phase 6 — `merge` as the reference tool

**Goal** One tool implemented completely, as the template for every other.

**Status** `not started`. **Depends on** Phase 5. **Completes** milestone M1.

**Input** `tools/merge/` and `tools/ocr/` exist on
[`m1-foundations`](reconciliation.md#component-disposition) as skeletons: real
`ToolSpec`, `Param` and validators, but both `run()` bodies raise
`NotImplementedError` and both signatures predate `cancellation`.

**Tasks** port the layout and metadata · **write `run()` against the current
`EngineStrategy`**, with required `progress` and `cancellation` · `cloud = None`
(pypdf-only) · CLI command wiring · golden tests.

**Definition of done** `docmax merge a.pdf b.pdf -o out.pdf` works end to end;
`-o a.pdf` is refused; the output validator checks that page count equals the sum
of the inputs *before* the swap.

---

## Phase 7 — Cloud client · Phase 8 — HTTP server

**Status** `not started`. **Depends on** Phase 6. **Milestone** M6.

**Both exist on [`m1-foundations`](reconciliation.md#component-disposition)**
and are ported rather than written. `cloud_client/` is complete and lint-clean;
`server/` implements routes, jobs, storage, auth and idempotency, with
`execution.start()` stubbed pending real tools.

Phase 7 brings `JobStatus` into `core/models.py` with the client, which needs
it. Phase 8 ports the server **together with its enforcement config** — that
config already satisfies every item under
[dependencies.md](../architecture/dependencies.md#not-yet-enforced), and cannot
land sooner because import-linter fails on a contract naming a module that does
not exist.

Client first, against the contract in [cloud-api.md](../cloud-api.md) and a mock;
then the server.

Where the server lives is settled:
[ADR 0006](../adr/0006-reference-server-location.md) places it at
`src/docmax/server/`, open and in this package. The execution-model and
observability decisions are **not** settled — see
[backlog](backlog.md#required) — and both should be recorded before the code,
not after.

Each ships with its enforcement in the same change: `independence` contract,
and a wheel exclusion. (`fastapi`/`mcp` in the forbidden list and the web
packages in `HEAVY_MODULES` landed early, in Phase 3's hardening pass — an
external-package rule needs no layer behind it.) See
[dependencies.md](../architecture/dependencies.md#not-yet-enforced).

---

## Phase 9 — TUI (M7)

**Status** `complete` — 2026-08-28. Milestone M7.

**Delivered** `docmax.tui` (app, generated forms, router bridge, catalog) ·
`docmax.pickers` (the two ADR 0005 pickers) · the `crop` tool and its `--box`
flag · `--interactive` on `crop` and `reorder` · `docmax tui` and the
bare-invocation guards · ADRs 0019–0021 · two new import-linter layers.

**It required no change below the interface layer.** That was this phase's own
test of the core contracts, and they passed: `ProgressSink` took a Textual
widget unmodified, `CancellationToken` took a keypress, and `ToolSpec`/`Param`
generated eighteen forms with no per-tool code. `core/`, `registry.py` and
`router.py` are untouched by this milestone.

**One finding, reported rather than worked around.** `ToolSpec` cannot say
"declared but not implemented", so a registry-driven tool list would offer `ocr`
and fail with a wrapped `NotImplementedError`. The TUI names that one exception
in `tui/catalog.py` with a test holding it, and the Core change is deferred — it
is the fourth instance of the same `ToolSpec` gap
[current-status.md](current-status.md) already says should be decided together.
See [ADR 0021](../adr/0021-the-tui-is-generated-from-the-registry.md).

**Two decisions changed enforced contracts**, so both were written before the
code stayed: [ADR 0019](../adr/0019-picker-package-and-rendering.md) placed
`pickers` as its own layer and settled how a page is rendered without vendoring
pdf.js; [ADR 0020](../adr/0020-tui-entry-point.md) settled the single narrow
import that lets the CLI start the TUI, and the three guards on a bare `docmax`.

**Definition of done**
- [x] `docmax tui` runs; a bare `docmax` opens it only at a real terminal
- [x] no `textual` → a typed error naming the install command, never a traceback
- [x] every screen generated from the registry; no per-tool code, asserted
- [x] every run goes through `EngineRouter`; no routing in the interface
- [x] cancellation leaves the destination untouched
- [x] pickers return parameters, write nothing, and have headless equivalents
- [x] `--json` and interactive sessions are mutually exclusive, both directions
- [x] `.importlinter` carries the two new layers; 5 contracts kept
- [ ] **CI matrix** — Windows / CPython 3.14 locally only, as for every phase
      since Phase 2

---

## M9 — pipelines, batch and folder watch

**Status** `complete` — 2026-08-28. Milestone M9. No phase number of its own, as
for M4, M5 and M8.

**Delivered** `docmax.runners` (`pipeline`, `batch`, `watch`) · the `pipeline`,
`batch` and `watch` commands · a sixth import-linter layer · ADRs 0023–0026.

**It required no change below the interface layer, and none inside it either.**
`core/`, `registry.py` and `router.py` are untouched by this milestone — the same
claim M7 made and the second time the core contracts have been tested by a
feature that had every reason to bend them. `EngineRouter.run` took a composed
caller unmodified, `OutputTarget.resolve` guarded every intermediate destination
as well as every final one, and `CancellationToken` stopped a batch between items
and a watch between ticks with no new mechanism.

**Two contracts wanted something `ToolSpec` cannot say**, and both were answered
the way [ADR 0021](../adr/0021-the-tui-is-generated-from-the-registry.md)
answered the same seam at M7: a frozenset in the consumer, held by a test, and no
Core change. `NOT_A_MIDDLE_STAGE` is the fifth appearance of that seam and
`SUFFIX_FROM_PARAMS` is the third of the three
[current-status.md](current-status.md) already says should be decided together.
This is now the strongest argument yet for deciding them.

**One row of the milestone was not delivered.** The roadmap says "resumable
batch"; `--resume` does not exist, and was deferred rather than invented — a
journal is a persistent app-owned format and deserves ADR 0008's treatment.
See [roadmap.md](roadmap.md#what-m9-did-not-deliver).

**Definition of done**
- [x] a pipeline composes tools without duplicating any tool's implementation
- [x] every stage goes through `EngineRouter`; no runner imports a tool
- [x] only the final stage writes a destination, and it goes through
      `core/atomic.py`; a failed or cancelled run leaves it untouched
- [x] batch refuses a plan that could write over an input, before any work
- [x] one failed document does not end a batch, and the typed error survives
- [x] the watcher cannot write into the folder it watches, in either direction
- [x] polling and settling use the standard library; no new dependency
- [x] `--json` remains one object on stdout, for all three commands
- [x] `.importlinter` carries the new layer; 5 contracts kept
- [ ] **CI matrix** — Windows / CPython 3.14 locally only, as for every phase
      since Phase 2

---

## Phase 10 — MCP server (M10)

**Status** `complete` — 2026-08-29. Milestone M10.

**Delivered** `docmax.mcp` (`schema`, `policy`, `server`) · the `docmax mcp`
command · the `mcp` extra (`mcp>=2.1,<3`) · a sixth interface layer entry and its
independence-contract membership · ADRs 0027–0030.

**It required no change below the interface layer**, which was this phase's own
stated test:

> If it requires a change below the interface layer, that is a signal the core
> contracts are wrong — treat it as a finding, not a workaround.

`core/`, `registry.py` and `router.py` are untouched. `iter_tools()` supplied the
whole tool surface, `Param.type_`'s closed set supplied the JSON Schema mapping
in four lines, `EngineRouter.run` took a protocol caller unmodified, and
`CancellationToken` absorbed the protocol's cancellation without a second
mechanism. **Three interfaces in a row — M7, M9, M10 — have now tested the core
contracts and none has needed one changed.**

**Two things were treated as findings rather than workarounds**, as this phase
instructed:

- **`input_suffixes`.** `docs/plans/05` assumes the field; `ToolSpec` does not
  have it. Not added — it would be a *fourth* seam beside the three
  [current-status.md](current-status.md) says should be decided together. The
  schema says "a path" instead. [ADR 0028](../adr/0028-the-mcp-tool-surface-is-the-registry.md).
- **An MCP `run_pipeline`.** Proposed by plan 05 and anticipated by ADR 0023, and
  impossible without a hand-written tool list, which ADR 0021 forbids. The
  registry rule won and the contradiction is recorded with a test holding the
  absence.

**One genuinely new piece of design**, and it is not a Core change: the policy
boundary. An MCP client is a program acting on someone's behalf, so reads and
writes are confined to `--root`, cloud is off unless asked for, and consent is
read but never written. [ADR 0029](../adr/0029-the-mcp-policy-boundary.md)
records why it lives at the interface rather than in `OutputTarget`.

**Definition of done**
- [x] `docmax mcp` serves every registered tool over stdio, generated from the
      registry, with no tool named anywhere in the package
- [x] a real client completes the handshake, lists tools and runs one end to end
      — asserted over an actual protocol session, not by calling handlers
- [x] reads and writes outside `--root` are refused before execution, including
      `..`, symlinks and lookalike sibling names
- [x] cloud engines are unreachable without `--allow-cloud`, and `--allow-cloud`
      cannot clear a configured `offline`
- [x] consent is never granted by the server
- [x] an existing destination is never overwritten; `force` is not exposed
- [x] cancellation reaches the existing `CancellationToken` and leaves no partial
      output
- [x] no traceback and no credential reaches a client
- [x] `lint-imports` covers `docmax.mcp`; 5 contracts kept
- [x] the SDK is optional, and `docmax mcp --help` works without it
- [ ] **CI matrix** — Windows / CPython 3.14 locally only, as for every phase
      since Phase 2

---

## Phase 11 — Remote MCP transport (M11)

**Goal** Make the MCP tool surface reachable by a client that cannot spawn a
local process — ChatGPT's connectors and similar — without weakening the M10
policy boundary for the local, stdio case.

**Status** `complete`. **Depended on** Phase 10.

[ADR 0035](../adr/0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md)
answers every question below and is **Accepted**: M11 is a route
(`src/docmax/server/routes/mcp.py`) inside `docmax.server`, not a new
interface, mounted at `POST /v1/mcp` and reusing the existing
upload/`file_id`/job model, bearer-token auth, and `RegistryRunner`. A review
of the initial draft found — and the accepted version closes — two gaps the
open-questions list below did not anticipate: a TLS requirement for this
route specifically, and session/auth binding (built on the MCP SDK's own
`StreamableHTTPSessionManager`, not a hand-rolled mechanism). See the ADR's
"Amended before acceptance" note and §6.

**Why this is not "Phase 10 with a different transport"** M10's schema takes
`inputs` as local filesystem paths, checked against `--root`. A remote caller
shares no filesystem with the server, so the tool contract itself has to
change, not just the wire transport. See
[roadmap.md](roadmap.md#m11--why-it-isnt-just-m10-with-a-different-transport).

**Open questions the ADR must answer**

- **File reference shape.** Does a remote MCP session reuse `docmax.server`'s
  existing upload → `file_id` → job model, or define its own? Reusing it avoids
  a second implementation of the same problem; not reusing it avoids coupling
  two interfaces that are currently independent (see the `independence`
  import-linter contract between `cli` and `server`).
- **Whether this is a fourth interface or a transport bridge in front of the
  third.** `docmax.server` is already network-reachable (ADR 0006). M11 might
  be "mount an MCP-speaking adapter in front of the existing REST API" rather
  than a new peer interface with its own dependency on `core`/`tools`.
- **Auth model.** Who may open a remote MCP session, and with what credential.
  ADR 0029's policy assumes one trusted local caller; that assumption does not
  survive the network.
- **Whether "roots" mean anything remotely.** `--root` confines a local
  filesystem walk. A remote client has no filesystem to confine — the
  equivalent guarantee (which uploads a session may reference, whether a
  session can reach another session's files) needs its own design, not a port
  of ADR 0029's check.
- **Consent and offline, remotely.** M10 never lets an agent grant cloud
  consent on the user's behalf. A remote session raises the same question with
  a less trustworthy caller — a network client is a program acting on behalf of
  someone the operator may not know at all.

**Definition of done**

- [x] an ADR is accepted, answering every question above — ADR 0035
- [x] a remote client can list tools and run one end to end over the chosen
      transport — `tests/unit/test_m11_mcp.py::test_a_tool_call_over_mcp_runs_through_the_same_registry_runner`
- [x] the local, stdio path (M10) is unchanged — this phase adds a capability,
      it does not modify ADR 0029's guarantees — `docmax/mcp/server.py` and
      `policy.py` are untouched; only `docmax/mcp/schema.py` changed, into a
      re-export of the relocated `docmax.mcpschema`
- [x] whatever auth model is chosen has a test asserting an unauthenticated or
      unauthorized session cannot reach a tool —
      `test_mcp_route_requires_a_valid_key`,
      `test_a_session_cannot_be_reused_by_a_different_key`
- [x] `lint-imports` covers whatever new module this becomes — `docmax.mcpschema`
      is in `layers` and `core-is-standalone`; `interfaces-are-independent` is
      unchanged
- [x] the roadmap and this phase are updated to `complete` together

**Found during review, closed before this phase is called done, not filed for
later:** job records and idempotency keys were not owner-scoped the way
uploads were, TLS was not required for a route built for a caller the
operator does not control, and nothing bound an MCP session to the credential
that opened it. All three are now enforced — see ADR 0035 §4 and §6 and its
Enforcement section for the specific tests.

**Named as follow-up, not built here** (`backlog.md`): rate limiting/quotas
per key, automatic reaping of jobs and stored bytes on a timer rather than a
manually-invoked `reap()`, and per-key tool authorization beyond the existing
cloud-engine filter — all three need a real per-user identity model this
milestone deliberately did not build (a flat bearer token stands in for one),
named in ADR 0035 as the boundary of what "give each caller its own bearer
token" can close on its own.

---

## Phase 12 — Contributor experience

**Goal** A new contributor can go from clone to merged pull request without
personal guidance.

**Status** `not started`.

**Tasks** `docs/development/{setup,workflow,testing,contributing}.md` · document
how to add a tool, an interface, and a cloud integration.

**Definition of done** the question "where does this new functionality belong?"
is answerable from the documentation alone.

---

## Working rules

Applies to every phase.

1. **A phase is not done until its documentation is.** Update
   `current-status.md`, and the changelog when the change is user-visible.
2. **A rule and its enforcement land together.** Adding a layer without its
   import-linter contract creates a rule that exists only in prose.
3. **Write the ADR before the code** when the decision is architectural.
   Afterwards it is a justification, not a decision.
4. **Commit logical milestones**, not the whole phase at once and not every edit.
5. **State what was verified and what was not.** Under the current network
   constraint, "the tests pass" is often not something that can be claimed
   locally — say so rather than implying it.
