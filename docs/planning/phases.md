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
- `docs/architecture/` — `overview.md` (moved from `docs/architecture.md`),
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

**Status** `not started`.

**Depends on** Phase 0.

**Why it is next** It unblocks every other phase, and it is pure standard
library — which under the current
[network constraint](current-status.md#blocked) makes it the only substantial
work that can actually be verified locally.

**Order matters.** Each step consumes the previous one:

| # | Module | Delivers |
|---|---|---|
| 1 | `core/models.py` | `DocumentRef`, `OutputTarget`, `ToolResult`, `Engine` |
| 2 | `core/protocols.py` | `ProgressSink`, `EngineStrategy`, `Validator` |
| 3 | `core/atomic.py` | `atomic_write`, `atomic_path`, `atomic_dir` |
| 4 | `core/cancellation.py` | `CancellationToken`, `NEVER_CANCELLED` |

**Tests required**
- unit tests per module, written against the *failure* each mechanism prevents —
  a happy-path test for `atomic_write` demonstrates nothing
- `OutputTarget.resolve` must be tested against in-place overwrite specifically
- cancellation must be tested under threads, not only sequentially
- `test_no_direct_writes.py` stops exempting a file that does not exist

**Documentation required** `implementation/core.md`; update
`current-status.md`.

**Definition of done** ADR 0003's guarantees hold under test on all three
platforms: a crash mid-write leaves the destination untouched, an input can
never be an output, and a cancelled multi-file operation leaves no partial
directory.

---

## Phase 3 — Configuration

**Goal** One configuration strategy with one precedence chain.

**Status** `not started`. **Depends on** Phase 2.

**Precedence**, lowest to highest:

```
defaults  →  config file (TOML)  →  environment  →  CLI/runtime override
```

**Tasks** `core/config.py` · locate the config directory via `platformdirs` and
`CONFIG_DIR_NAME` · the `offline` flag · per-tool engine preference · the
per-tool consent record.

**Constraint** Environment access is centralised here. Scattered `os.environ`
reads are what this phase exists to prevent — see
[layers.md](../architecture/layers.md#cross-cutting-concerns).

**Definition of done** precedence is tested at every level; `offline = true`
makes cloud unreachable *regardless of flags*, including an explicit
`--engine cloud`.

---

## Phase 4 — Tool registry

**Goal** Lazy discovery, per [ADR 0002](../adr/0002-registry-mechanism.md).

**Status** `not started`. **Depends on** Phase 2.

**Tasks** `core/registry.py` — `ToolSpec`, `Param`, `register`, directory scan of
`tools/*/tool.py`, `importlib.metadata` entry points for third-party tools,
strategy loading on demand.

**Definition of done** un-skip
`test_building_the_registry_pulls_in_nothing_heavy` and have it pass: building
the full registry imports no tool implementation and no heavy dependency.

---

## Phase 5 — Engine router

**Goal** The single orchestration path both the CLI and the server call.

**Status** `not started`. **Depends on** Phases 2, 3, 4.

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

**Tasks** `tools/merge/{tool,local,validators}.py` · `cloud = None` (pypdf-only)
· CLI command wiring · golden tests.

**Definition of done** `docmax merge a.pdf b.pdf -o out.pdf` works end to end;
`-o a.pdf` is refused; the output validator checks that page count equals the sum
of the inputs *before* the swap.

---

## Phase 7 — Cloud client · Phase 8 — HTTP server

**Status** `not started`. **Depends on** Phase 6. **Milestone** M6.

Client first, against the contract in [cloud-api.md](../cloud-api.md) and a mock;
then the server. Both are governed by decisions not yet recorded — see
[backlog](backlog.md#required). **Write the ADRs before the code**: the server's
location relative to the open-core line contradicts
[ADR 0004](../adr/0004-open-core-boundary.md) as currently written, and that has
to be resolved deliberately rather than by whichever file is edited first.

Each ships with its enforcement in the same change: `independence` contract,
`fastapi`/`mcp` added to the forbidden list, web packages added to
`HEAVY_MODULES`, and a wheel exclusion. See
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
