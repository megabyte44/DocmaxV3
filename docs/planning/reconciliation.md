# Reconciling `m1-foundations`

The working record behind [ADR 0007](../adr/0007-m1-foundations-reconciliation.md),
which is the decision. This document is the evidence and the outstanding list.

**Delete this file when the table below is empty.** A reconciliation document
that outlives its reconciliation is the stale planning artefact this system
exists to avoid.

---

## The divergence

Two lines fork cleanly at `4fc92f2` (M0) and share no commit. `git log
--cherry-pick` finds no equivalent pairs — this is a genuine fork, not a rebase.

| | `architecture` | `m1-foundations` |
|---|---|---|
| Commits beyond M0 | 3 | 6 |
| Scope | Phases 0–2 | all of M1, attempted at once |
| Core contracts | rebuilt in Phase 2, verified | own earlier copy |
| Registry / cloud client / server / tools | none | all present |
| Toolchain state | `ruff`, `mypy`, `lint-imports` clean | 12 `ruff` errors (below) |

### Why they diverged

`m1-foundations` was written first and attempted the whole of M1 in one line:
Core, registry, client, server and tools together. It left the working tree
before it was reviewed, and the tree was reset to M0. The phase system was then
introduced, and Phase 2 rebuilt the Core contracts alone, deliberately narrow,
under a working toolchain.

So the two are not competing designs. They are the same design, built at
different scopes — which is why the overlap is near-identical and the
non-overlap is complementary.

---

## Measured evidence

Not asserted — run. Current `ruff` against `m1-foundations`' source tree:

```
core/atomic.py         PTH105 ×4      os.replace() should be Path.replace()
core/cancellation.py   RUF100 ×4      noqa for rules this project does not enable
core/cancellation.py   SIM105, S110   try/except/pass in cancel()
cloud_client/models.py I001           import block un-sorted
tools/ocr/validators.py F401          unused import
                       ─────────
                       12 errors
```

The first ten are **the same errors Phase 2 has already fixed** in the same two
files. That is the decisive datum: M1's Core is not a different implementation,
it is the *pre-fix copy* of this one. Discarding it loses nothing and avoids
re-introducing ten known defects.

The other two are in M1's unique work and are trivial. Note what is absent:
`core/registry.py`, all of `server/`, and `cloud_client/{client,config,errors}.py`
produce **zero** errors across roughly 1,700 lines.

---

## Layering audit

Every M1 component checked against the HLD direction
(Interfaces → Application/Tools → Cloud Client → Core).

| Check | Result |
|---|---|
| `core` imports nothing upward | ✅ registry imports only `core.*` |
| `cloud_client` imports only `core` + `httpx` | ✅ |
| `server` imports only `core.*` and `server.*` | ✅ |
| `server` does **not** import `cloud_client` | ✅ and contractually forbidden |
| `server` does **not** import `cli` | ✅ |
| Cloud SDK types leaking into `core` | ✅ none — `httpx` confined to the client |
| Tools import a UI framework | ✅ none |
| Heavy imports at module scope | ✅ `pypdf` is imported inside methods |

**No architectural violations found.** This is the reassuring result: M1 was
built against the same HLD, so the work is structurally compatible even where it
is textually stale.

---

## Component disposition

Strike a row when its component is ported. When the table is empty, delete this
file.

| Component | M1 state | Owner phase | Decision | Reason |
|---|---|---|---|---|
| `core/atomic.py` | duplicate | — | **discard M1 copy** | identical public API; M1's is the pre-lint version with 4 `PTH105` + 2 more |
| `core/cancellation.py` | duplicate | — | **discard M1 copy** | identical public API; same, 4 `RUF100` + `SIM105`/`S110` |
| `core/models.py` | duplicate + `JobStatus` | — / 7 | **keep current; take `JobStatus` at Phase 7** | otherwise identical. `JobStatus` was omitted from Phase 2 as speculative and becomes real with the client, which imports it |
| `core/protocols.py` | **older** | — | **keep current** | M1's `run()` takes `progress` optional and has **no `cancellation`**. Phase 2 made both required so no engine carries `if progress is not None` and every engine is cancellable. Do not regress this |
| `core/registry.py` | complete | **4** | **adopt, near as-is** | lazy `find_spec` walk + entry points, ADR 0002-compliant, lint-clean. Fits current Core unchanged |
| `cloud_client/` | complete | **7** | **adopt after re-reading against `cloud-api.md`** | clean layering; depends on `JobStatus`, so it lands with that |
| `server/` | real, one stub | **8** | **adopt** | routes, jobs, storage, auth, idempotency all implemented. `execution.start()` raises `NotImplementedError` pending real tools — correctly, it cannot run what does not exist |
| `tools/merge/`, `tools/ocr/` | skeletons | **6** | **adopt layout and metadata; rewrite `run()`** | `ToolSpec`, `Param`, validators and the `build()` factory are real. Both `run()` bodies raise `NotImplementedError`, and both signatures predate `cancellation` |
| Enforcement config | complete | **8** | **adopt wholesale** | see below — this is the single largest win |
| `docs/architecture.md`, `cloud-api.md` edits | superseded | — | **discard** | `architecture.md` has since moved to `architecture/overview.md` and been rewritten |

### The enforcement config is the largest single win

`m1-foundations` already implements **every** item the backlog lists as *not yet
enforced*, and every check [ADR 0006](../adr/0006-reference-server-location.md)
requires the server to arrive with:

- `docmax.server` in the layers contract, above `tools`
- `fastapi` and `mcp` added to `core-is-ui-free`
- an `interfaces-are-independent` contract (`cli` ↔ `server`)
- a `server-is-not-a-client` contract
- `docmax.server` added to `core-is-standalone`
- `exclude = ["docmax.server*"]` plus `tests/hygiene/test_wheel_excludes_server.py`
- a `server` extra, deliberately outside `all`
- `server` in `LIBRARY_PACKAGES`, so a request handler cannot exit the process

These cannot land before the server does — import-linter fails on a contract
naming a module that does not exist — so they arrive together at Phase 8.

**ADR 0006 was written without knowledge of this.** It reasoned from the
contract in `cloud-api.md` that the server belongs in-tree, open, and out of the
wheel. M1 had independently built exactly that. Two derivations, one answer.

---

## What this changes for later phases

| Phase | Was | Now |
|---|---|---|
| 3 — Configuration | unchanged | unchanged. `cloud_client/config.py` and `server/config.py` are worth reading first — both already resolve settings with an env prefix, and Phase 3 should not invent a third precedence chain |
| 4 — Registry | write from scratch | **port `core/registry.py`**, re-read against current Core, un-skip the registry import-safety test |
| 6 — `merge` | write from scratch | port the tool layout; write `run()` against the current `EngineStrategy` |
| 7 — Cloud client | write from scratch | port `cloud_client/`, bringing `JobStatus` with it |
| 8 — Server | write from scratch | port `server/` **and its enforcement config**, then implement `execution.start()` |

The phase order does not change. What changes is that four phases now start from
a draft rather than a blank file.

---

## Rules while this is outstanding

1. **No commits to `m1-foundations`.** It is a source, not a development line.
2. **Read before porting.** Every component predates Phase 2's Core and must be
   re-read against it — especially anything touching `EngineStrategy.run()`.
3. **Ported code passes the toolchain before it lands.** `ruff`, `mypy --strict`,
   `lint-imports`. That is what catches a stale Core copy arriving by accident.
4. **Strike the row.** An unstruck row is outstanding work.
