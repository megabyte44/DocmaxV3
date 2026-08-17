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
- [ ] **Execution model.** Whether jobs are in-process or queued, and what that
      means for cancellation crossing a process boundary.
- [ ] **Observability.** One approach to logging, and the mechanism that keeps
      cloud-api.md's "document contents are never logged" true rather than
      merely stated.

### Documentation the project promises but does not have

- [ ] `docs/development/setup.md`, `testing.md`, `contributing.md` — the README
      links contributors to ADRs and architecture, but never explains how to run
      anything
- [x] ~~`docs/implementation/core.md`~~ — written in Phase 2
- [ ] `benchmarks/` — the README promises published benchmarks with real
      hardware and methodology, and no numbers appear until they are measured

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

- [ ] **Pipelines and resumable batch** (M9). Needs the cancellation and progress
      contracts to survive crossing a process boundary.
- [ ] **Folder watch** (M9). v2's version fed on its own output; the replacement
      needs a rule about outputs written into a watched directory.
- [ ] **Third-party tool plugins.** The entry-point group exists in
      `branding.py`; the trust model for third-party code does not.
- [ ] **`--json` everywhere** (M6). `ErrorCode` is already described as public
      API, so this partly exists as a commitment already.
- [ ] **`docmax setup`** (M3) — idempotent, `--dry-run`, verifies afterwards
      rather than assuming success.

---

## Exploratory

Ideas. No commitment, and none of these should be treated as planned.

- [ ] A local HTTP mode for the TUI's visual pickers — see
      [ADR 0005](../adr/0005-gui-pickers.md).
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
