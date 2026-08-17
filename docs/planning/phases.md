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

**Status** `not started` — **next**. **Depends on** Phases 2, 3, 4, all complete.

It is now the only Core module missing, and both callers already exist: the CLI
and `server/execution.py`, whose `start()` is stubbed waiting for exactly this.

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

## Phase 9 — TUI (M7) · Phase 10 — MCP server (M10)

**Status** `not started`.

Both are additional drivers of the same core. Neither may import another
interface. If either requires a change below the interface layer, that is a
signal the core contracts are wrong — treat it as a finding, not a workaround.

---

## Phase 11 — Contributor experience

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
