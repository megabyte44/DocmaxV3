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

## Verification

Network access was restored on 2026-08-15 and **the full toolchain now runs
locally**. Every check the project defines passes:

| Check | Result |
|---|---|
| `pytest -m "not golden and not needs_binary"` | **114 passed, 3 skipped** |
| `ruff check .` | **passed** |
| `ruff format --check .` | **passed** — 47 files already formatted |
| `mypy` (strict) | **passed** — no issues in 27 source files |
| `lint-imports` | **passed** — 3 contracts kept, 0 broken |

The three skips are intentional: two are the self-exemptions inside the hygiene
suite (`branding.py` may contain brand literals; `atomic.py` may write), and the
third is the registry import-safety test, which stays skipped until Phase 4.

Environment: Windows, CPython 3.14, `.venv` from `pip install -e ".[dev]"`.

**Still unverified:** the CI matrix — Linux and macOS, and Python 3.11–3.13.
Everything above is one platform and one interpreter, and 3.14 is not in the
project's supported matrix. The `golden` and `needs_binary` tests are also
unrun, since the external binaries are absent locally; CI requires them.

### What the toolchain caught

Running the real tools after the fact found 17 `ruff` errors and 8 `mypy`
errors, **all in Phase 2 code, none previously visible** to the standalone
verifier. Worth recording because it calibrates how much a hand-rolled check is
worth:

- `PTH105` — `os.replace()` should be `Path.replace()`; the project enables the
  pathlib ruleset and the module had four call sites.
- `RUF100` — `noqa: SLF001` / `BLE001` directives for rules this project does
  not enable, which are themselves errors.
- `SIM105` / `S110` — `try/except/pass` in `cancel()`, now `contextlib.suppress`.
- `PT012` ×4, `PT017`, `N818` — pytest and naming conventions in the tests.
- `mypy` — comparing a `StrEnum` member to a string literal under
  `strict_equality`; asserting on the return of a `-> None` method; and one
  genuine narrowing trap, where `assert not token.is_cancelled` caused mypy to
  treat the rest of the test as unreachable.

All were fixed rather than suppressed.

---

## Blocked

Nothing. The earlier network constraint is resolved — `pip` reaches PyPI and the
full toolchain is installed and passing.

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

## Related branch — `m1-foundations`

An earlier M1 implementation was written on 2026-08-15 and disappeared from the
working tree, which was at the time recorded here as "discarded". **That was
wrong, and this corrects it.** The work is committed and intact on the
`m1-foundations` branch:

```
a0c3e52  docs: record the three-part shape and the fifth structural guarantee
d3c7f65  feat(server): reference implementation of the Cloud Engine API
873467d  feat(tools): merge and ocr as the two reference tool layouts
1d5163c  feat(cloud_client): the client half of the Cloud Engine contract
82cf226  feat(core): atomic writes and cancellation
be3e3b6  feat(core): value types, protocols, and the lazy tool registry
```

It branches from `4fc92f2` and shares no commit with the phase work, so the two
are independent lines over the same milestone.

**This needs reconciling before Phase 4.** `m1-foundations` contains a registry,
a cloud client, a server and two tool skeletons — Phases 4, 7, 8 and 6
respectively — and its `core` overlaps with what Phase 2 built. Whether to merge
it, cherry-pick from it, or treat it as a reference and supersede it is an open
question, and is tracked in [the backlog](backlog.md#required).

Doing nothing is the one option that is not safe: two divergent implementations
of `core` in one repository is precisely the drift this documentation system
exists to prevent.

Stale bytecode directories left behind at `src/docmax/server/`,
`tools/merge/` and `tools/ocr/` — source-less `__pycache__` shells that made
those packages look present in a directory listing — have been removed.

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
