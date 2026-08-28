# Backlog

Work that is known about but not scheduled. Sequenced work lives in
[phases.md](phases.md); this is everything else.

The four sections are a commitment level, not a priority. **Nothing moves from
Future to Required by being implemented** — it moves by being decided, and the
decision gets written down first.

---

## Required

Known gaps in things the project already claims. Each is a rule the architecture
implies with nothing enforcing it, or a promise made in a document that the code
does not keep.

### Enforcement

**All complete.** Every rule listed here through Phases 1–3 now has a check:
the `docmax.server` layer entry, `fastapi`/`mcp` in `core-is-ui-free`, the
`interfaces-are-independent` and `server-is-not-a-client` contracts, the wheel
exclusion with `test_wheel_excludes_server.py`, `server` in `LIBRARY_PACKAGES`,
and the web frameworks and cloud SDKs in `HEAVY_MODULES`.

They arrived with the server on `main` — see
[ADR 0009](../adr/0009-main-is-the-base.md) — which is what the architecture
required: a layer lands with its contract.

### Decisions owed an ADR

These are architectural and currently unrecorded. Writing the code first would
make the ADR a justification rather than a decision.

- [x] ~~**Where the server lives, and which side of the open-core line.**~~
      Resolved by [ADR 0006](../adr/0006-reference-server-location.md): the
      reference server lives at `src/docmax/server/` and is open; `pro/` is
      unchanged. Its enforcement is listed above and lands with the layer.
- [x] ~~**Reconcile the `m1-foundations` branch.**~~ Settled by
      [ADR 0009](../adr/0009-main-is-the-base.md), which supersedes ADR 0007:
      the branch was already merged into `main` and released before ADR 0007 was
      written. `main` is the base.
- [x] ~~**Configuration precedence and consent storage.**~~ Decided by
      [ADR 0008](../adr/0008-consent-record.md) and implemented in Phase 3:
      app-owned `consent.json` beside the user-owned `config.toml`, scoped to
      `(tool, endpoint)` and a hand-bumped terms version, failing closed.
- [x] ~~**Execution model.**~~ Settled by
      [ADR 0016](../adr/0016-jobs-run-in-process.md) at M6: the reference server
      runs jobs in-process, synchronously, with an in-memory job store. The wire
      contract already permits a queued implementation to answer `202` later
      without a client change, which is what makes the decision reversible.
- [ ] **Observability.** One approach to logging, and the mechanism that keeps
      cloud-api.md's "document contents are never logged" true rather than
      merely stated.

### Documentation the project promises but does not have

- [ ] `docs/development/setup.md`, `testing.md`, `contributing.md` — the README
      links contributors to ADRs and architecture, but never explains how to run
      anything
- [x] ~~`docs/implementation/core.md`~~ — written in Phase 2
- [ ] `benchmarks/` — **the harness landed at M6** and the methodology is
      written down, but **no numbers have been measured**: the development
      machine has neither Ghostscript nor Pandoc. Publishing needs a machine
      with both, and CI deliberately does not publish.

### Environment

- [x] ~~**Restore network access.**~~ Resolved 2026-08-15; the full toolchain
      installs and runs locally.

---

## Important

Worth doing, not blocking anything.

- [ ] **Redundant branches.** `architecture`, `feat/core-foundation`,
      `phase-2/core-contracts` and `docs/architecture-and-planning` are all
      superseded by this reconciliation. Deleting them is a separate
      decision; none has been touched.
- [ ] **Concurrency for batch.** Deliberately not built at M9 —
      [ADR 0025](../adr/0025-batch-mirrors-names-into-an-output-directory.md)
      records why, and names the three contracts that would have to change:
      `CancellationToken` across a process boundary, `ProgressSink` describing N
      simultaneous items, and `ConsoleProgress`'s single live region. Worth doing
      only against a measured need; a 200-file OCR batch is the obvious one.
- [ ] **Recursive folder watch.** M9 watches one directory, non-recursively. A
      recursive watch turns ADR 0026's containment rule into a subtree question
      and should be decided with that in mind rather than added.
- [ ] **Python 3.14 in the CI matrix.** The development machine runs 3.14; CI
      tests 3.11–3.13. The gap means local runs exercise a version CI does not.
- [ ] **A `conftest.py` with shared fixtures.** Currently every test builds its
      own temp paths.
- [ ] **Property tests** for the parsers, per the `property` marker that exists
      and is unused.
- [ ] **The fixture corpus generator** (`tests/fixtures/generate.py`) referenced
      by `.gitignore` but absent.
- [ ] **Coverage floor in CI.** `--cov` runs; nothing fails on a drop.
- [x] ~~**Stale `__pycache__` cleanup.**~~ Removed 2026-08-15.

---

## Future

Decided in principle, not scheduled. Mostly already on the
[roadmap](roadmap.md) — listed here where they carry an open question.

- [x] ~~**Pipelines and batch** (M9).~~ Delivered 2026-08-28. The open question
      resolved itself: batch is **serial**, so the cancellation and progress
      contracts never cross a process boundary and needed no change. See
      [ADR 0025](../adr/0025-batch-mirrors-names-into-an-output-directory.md),
      which also records what revisiting that would cost.
- [x] ~~**Folder watch** (M9).~~ Delivered 2026-08-28. The rule the backlog
      asked for is containment in both directions — the output directory may not
      be inside the watched tree, nor the watched tree inside it.
      [ADR 0026](../adr/0026-the-watcher-polls-and-never-watches-its-own-output.md).
- [ ] **A resume journal, and `--resume`** (deferred from M9). The roadmap row
      says "resumable batch" and no journal exists. It needs a decided location,
      a schema version, and a defined behaviour on a corrupt or future-version
      record — the treatment [ADR 0008](../adr/0008-consent-record.md) gave the
      consent record. `core/errors.py`'s `CancelledError` docstring promises it,
      and is currently ahead of the code. The watcher's in-memory processed-set
      is the same gap seen from the other side.
- [ ] **Third-party tool plugins.** The entry-point group exists in
      `branding.py`; the trust model for third-party code does not.
- [x] ~~**`--json` everywhere**~~ — delivered at M6.
      [ADR 0017](../adr/0017-json-output-contract.md) and
      [implementation/json.md](../implementation/json.md).
- [ ] **`docmax setup`** (M3) — idempotent, `--dry-run`, verifies afterwards
      rather than assuming success.

---

## Exploratory

Ideas. No commitment, and none of these should be treated as planned.

- [x] ~~A local HTTP mode for the TUI's visual pickers.~~ This was never
      exploratory — [ADR 0005](../adr/0005-gui-pickers.md) had already accepted
      localhost HTTP as *the* implementation, and this line contradicted it.
      Delivered at M7; see [ADR 0019](../adr/0019-picker-package-and-rendering.md)
      for where the package lives and how a page is rendered.
- [ ] Content-addressed caching of expensive operations (OCR, compression).
- [ ] Streaming operations for documents larger than memory.
- [ ] A `docmax explain` that prints which engine would run and why, without
      running it — mostly a debugging aid for the router's precedence rules.

---

## Conventions

- An item states the *problem*, not a chosen solution, unless the solution is
  the decision.
- Anything affecting architecture becomes an ADR before it becomes code.
- Delete items that are done. This file is not a history; that is what
  [the changelog](../../CHANGELOG.md) and `docs/adr/` are for.
