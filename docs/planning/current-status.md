# Current status

**Last updated:** 2026-08-15 · **Branch:** `phase-2/core-contracts` · **Base:** `4fc92f2`

This document describes what is true right now, not what is intended. If it
disagrees with the repository, the repository is right and this file is stale —
fix it.

---

## Where the project is

**Phases 0, 1 and 2 are complete. Milestone M1 is in progress.**

| Phase | | |
|---|---|---|
| 0 — Repository baseline | complete | commit `4fc92f2` |
| 1 — Architecture mapping and docs | complete | this documentation system |
| 2 — Core contracts | complete | `models`, `protocols`, `atomic`, `cancellation` |
| 3 — Configuration | not started | next |

### Core as it stands

| Module | State |
|---|---|
| `branding.py` | done (M0) — unchanged by Phase 2 |
| `errors.py` | done (M0) — unchanged; already carried every error Phase 2 raises |
| `models.py` | done — `DocumentRef`, `OutputTarget`, `ToolResult`, `Engine` |
| `protocols.py` | done — `ProgressSink`, `NullProgress`, `EngineStrategy`, `Validator` |
| `atomic.py` | done — `atomic_write`, `atomic_path`, `atomic_dir` |
| `cancellation.py` | done — `CancellationToken`, `NEVER_CANCELLED` |
| `config.py` | **missing** — Phase 3 |
| `registry.py` | **missing** — Phase 4 |
| `router.py` | **missing** — Phase 5 |

Everything else is unchanged from M0: `tools/` and `cloud_client/` are still
docstrings with no implementation, and the CLI still has only `doctor`.

`test_building_the_registry_pulls_in_nothing_heavy` remains skipped — correctly,
since `core/registry.py` is Phase 4. It should be un-skipped there, not sooner.

---

## Verification — what was and was not run

**This is the honest picture, and it is partial.**

### Ran successfully

- `python -m py_compile` over all core and test modules — clean.
- **A standalone verifier reproducing the four hygiene tests' logic** against the
  real source tree, plus behavioural checks of every Phase 2 contract:
  **76 checks, all passing**, on Windows / CPython 3.14. Covers: no process
  exits in library code; no writes outside `atomic.py`; no brand literals
  outside `branding.py`; import safety per module by subprocess probe; and the
  behaviour of models, protocols, atomic writes and cancellation — including
  Ctrl-C mid-write, cancelled multi-file runs, and sixteen threads racing on
  `cancel()`.
- Markdown link integrity across the repository — 69 relative links, all resolve.

### Blocked by the environment, not by the project

`pip` cannot reach PyPI on this machine (DNS resolution fails), so the dev
extras are not installed. **None of the following has been run, and no claim is
made about them:**

| Command | Status |
|---|---|
| `pytest` | **not run** — the test files have never executed under pytest |
| `ruff check .` | **not run** |
| `ruff format --check .` | **not run** |
| `mypy` | **not run** |
| `lint-imports` | **not run** |

The standalone verifier runs the same *checks* the hygiene tests encode, but it
is not pytest and does not exercise fixtures, parametrisation, or the test files
themselves. Treat Phase 2 as **verified in behaviour, unverified in toolchain**.

### What to run when a network is available

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
pytest -m "not golden and not needs_binary"
ruff check . && ruff format --check .
mypy
lint-imports
```

Most likely to surface something: `mypy --strict` over the new modules, and
`ruff` on line length and import ordering. `lint-imports` should pass unchanged —
Phase 2 added no import edges between layers.

---

## Blocked

Nothing is blocked on a decision. One environmental constraint stands, above.

---

## Architecture violations and gaps

No violations of the *enforced* rules. Phase 2 added no cross-layer import edge;
`core` still imports only the standard library and itself.

Unenforced rules — all blocked on layers that do not exist, each to land with the
layer it governs, per
[dependencies.md](../architecture/dependencies.md#not-yet-enforced):

| Gap | Lands with |
|---|---|
| `core` may import `fastapi` / `mcp` — not in the forbidden list | `server` / `mcp` |
| No `independence` contract between interfaces | the second interface |
| `HEAVY_MODULES` omits web-framework packages | `server` |
| `docmax.server` has no wheel exclusion | `server` |
| No single configuration strategy | Phase 3 |
| No observability boundary | Phase 8 |

---

## Documentation status

| Document | State |
|---|---|
| `architecture/overview.md` | current — `EngineStrategy` signature corrected to match the code |
| `architecture/layers.md` | current — Core status updated |
| `architecture/dependencies.md` | current |
| `implementation/core.md` | current — new in Phase 2 |
| `adr/README.md` | current — indexes 0006 |
| `planning/*` | current |
| `cloud-api.md` | design-stage; no server exists |
| `README.md` | current |
| `CHANGELOG.md` | current through Phase 2 |
| `development/*` | **missing** — no setup, testing or contributing guide |
| `api/*` | not needed until the HTTP layer exists |

---

## Decisions

[ADR 0006](../adr/0006-reference-server-location.md) resolved where the reference
server lives: `src/docmax/server/`, open, in this package. It supersedes the
`cloud_server/` clause of [ADR 0004](../adr/0004-open-core-boundary.md); the rest
of 0004 stands, and its text is unchanged apart from a pointer.

Three decisions remain owed an ADR before the code that needs them — configuration
precedence and consent storage, the execution model, and observability. See
[backlog.md](backlog.md#decisions-owed-an-adr).

---

## Session note — discarded work, 2026-08-15

An earlier M1 implementation written during a session on this date was discarded
and the tree returned to `4fc92f2`. Phase 2 rebuilt `models`, `protocols`,
`atomic` and `cancellation` from that work; the `cloud_client`, `server` and tool
skeletons were **not** rebuilt, as they belong to later phases.

Empty `__pycache__` directories from the discarded work remain on disk at
`src/docmax/server/`, `tools/merge/` and `tools/ocr/`. They are git-ignored and
contain no source, but they make those packages look present in a directory
listing. Removing them is in [the backlog](backlog.md#important).

---

## Next

**Phase 3 — Configuration.** One strategy, one precedence chain: defaults →
config file → environment → runtime override. It owns the `offline` flag, the
per-tool engine preference, and the consent record — all of which Phase 5's
router reads, so it must precede the router.

It needs a decision recorded first: where the consent record lives and what
invalidates it. See [phases.md](phases.md#phase-3--configuration).

**Do not start it without direction** — Phase 2 was scoped explicitly, and the
next phase is to be decided separately.
