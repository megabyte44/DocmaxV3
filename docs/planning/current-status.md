# Current status

**Last updated:** 2026-08-15 · **Branch:** `main` · **HEAD:** `4fc92f2`

This document describes what is true right now, not what is intended. If it
disagrees with the repository, the repository is right and this file is stale —
fix it.

---

## Where the project is

**Milestone M0 is complete and committed. M1 has not started in the tree.**

The working tree is clean and identical to `4fc92f2`. Everything below is
implemented, tested and enforced in CI.

| Area | State |
|---|---|
| Architecture, layering, ADRs 0001–0005 | done |
| Typed error hierarchy (`core/errors.py`) | done |
| Branding single-source (`core/branding.py`) | done |
| CLI shell + `doctor` (`cli/main.py`, `cli/render.py`) | done |
| Four structural hygiene tests | done |
| CI: lint, 3×3 test matrix, golden, open-core | done |

---

## What is not built yet

Everything M1 needs. `core` currently contains `errors.py` and `branding.py`
only; `tools/` and `cloud_client/` are docstrings with no implementation.

| Missing | Referenced by | Consequence |
|---|---|---|
| `core/registry.py` | ADR 0002, `tools/__init__.py`, `branding.py` | no tool can be discovered |
| `core/models.py` | ADR 0003, `architecture/overview.md` | no `DocumentRef` / `OutputTarget` / `ToolResult` |
| `core/protocols.py` | `overview.md`, `cli/render.py` | no `ProgressSink` / `EngineStrategy` |
| `core/atomic.py` | ADR 0003, `test_no_direct_writes.py` | the write-hygiene test exempts a file that does not exist |
| `core/cancellation.py` | `errors.CancelledError` | nothing can be cancelled |
| `core/router.py` | `overview.md` (engine resolution) | no orchestration; each interface would have to invent its own |
| `core/config.py` | `overview.md` (privacy rules) | no `offline` flag, no consent record |
| any tool | the roadmap | `docmax` can report on binaries and nothing else |

`test_building_the_registry_pulls_in_nothing_heavy` is currently skipped with
`reason="enabled in M1, once core/registry.py exists"`, so the suite is green
and internally consistent.

---

## Blocked

Nothing is blocked on a decision. One environmental constraint applies:

> **No network access on the current development machine.** `pip` cannot reach
> PyPI, so `pytest`, `ruff`, `mypy` and `import-linter` cannot be installed or
> run locally. Pure-stdlib modules can be exercised directly with the system
> interpreter; anything needing a third-party package can only be verified in
> CI. Treat local "verification" claims accordingly until this is resolved.

---

## Architecture violations and gaps

No violations of the *enforced* rules — CI is green and the layering holds.

The gaps are rules that follow from the architecture with **nothing enforcing
them yet**. All of them are blocked on layers that do not exist, and each should
land in the same change as the layer it governs:

| Gap | Lands with |
|---|---|
| `core` may import `fastapi` / `mcp` — not in the forbidden list | `server` / `mcp` |
| No `independence` contract between interfaces | the second interface |
| `HEAVY_MODULES` does not include web-framework packages | `server` |
| `docmax.server` has no wheel exclusion | `server` |
| No single configuration strategy | `core/config.py` |
| No observability boundary — yet cloud-api.md forbids logging document contents | `server` |

Tracked in [backlog.md](backlog.md#required).

---

## Documentation status

| Document | State |
|---|---|
| `architecture/overview.md` | current — moved from `docs/architecture.md` |
| `architecture/layers.md` | current |
| `architecture/dependencies.md` | current; marks unenforced rules explicitly |
| `adr/README.md` | current |
| `planning/*` | current |
| `cloud-api.md` | design-stage; states clearly that no server exists |
| `README.md` | current; roadmap table now links to `planning/roadmap.md` |
| `CHANGELOG.md` | current for M0 |
| `development/*` | **missing** — no setup, testing or contributing guide |
| `api/*` | not needed until the HTTP layer exists |

---

## Session note — discarded work, 2026-08-15

A substantial M1 implementation was written during a working session on this
date and **was subsequently discarded**; the tree was returned to `4fc92f2`.
Recorded here because the artefacts are gone but the effort is not free to
repeat, and because empty `__pycache__` directories from it remain on disk at
`src/docmax/server/`, `src/docmax/tools/merge/` and `src/docmax/tools/ocr/` —
these are ignored by git and contain no source.

The work covered: `core/models.py`, `core/protocols.py`, `core/registry.py`,
`core/atomic.py`, `core/cancellation.py`, a `cloud_client` implementation, a
FastAPI `server` package, and `merge`/`ocr` tool skeletons.

Two findings from it are worth keeping regardless of the code:

1. **`core/atomic.py` and `core/cancellation.py` were implemented and verified**
   against 27 and 38 behavioural checks respectively, including Ctrl-C
   mid-write, cancelled multi-file runs, and concurrent `cancel()` across
   sixteen threads. The designs are known to work on Windows / Python 3.14.
2. **A staged-file prefix is a brand literal.** `".docmax-"` written directly
   fails `test_branding.py`; it must derive from `CLI_NAME`. Easy to hit twice.

No decision is implied here about whether to rewrite it. See
[phases.md](phases.md) for the sequence if it is resumed.

---

## Next

**Phase 2 — Core contracts.** It unblocks everything else, needs no third-party
dependency, and is therefore the only substantial work that can be *verified*
under the current network constraint.

Order within it matters: `models` → `protocols` → `atomic` → `cancellation` →
`registry` → `router`. The router is last because it consumes all of them, and
`config` must precede it because engine resolution reads the `offline` flag and
the consent record.

See [phases.md](phases.md#phase-2--core-contracts).
