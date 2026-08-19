# ADR 0009 — `main` is the base; ADR 0007 was overtaken by events

**Status:** Accepted · 2026-08-16
**Supersedes:** [ADR 0007](0007-m1-foundations-reconciliation.md) in full.

## Context

[ADR 0007](0007-m1-foundations-reconciliation.md) was ratified on 2026-08-16. It
recorded that `m1-foundations` would be **preserved as a read-only source
branch, never merged**, and absorbed component by component by the phase owning
each.

That decision was already false when it was written, and nobody involved knew.

`m1-foundations` had been merged into `main` via **PR #1**, and seven releases
had shipped on top of it — `v3.0.0a1` through `v3.0.0a7` — including a rename of
the distribution to **`DocmaxV3`** and its publication to PyPI. The phase work
had been developing on a line forked from `4fc92f2` (M0) that never fetched, so
`origin/main` moving 23 commits ahead went unnoticed until a routine
`git fetch` during the `feat/core-foundation` task.

This is worth stating plainly rather than tidying away: **ADR 0007's central
factual premise was wrong**, and a decision document that contradicts the
repository is worse than no document, because it is trusted.

## Decision

**`origin/main` is the authoritative base. The phase line is reconciled onto it,
not the other way round.**

`m1-foundations` is not a source branch to be drawn from — it is already *in*
`main`, released. ADR 0007 is superseded in full: its mechanism (port component
by component) is moot because the components arrived by merge, and its premise
(never merged) is contradicted by the repository.

What `main` keeps, because it is released and validated:

- the `DocmaxV3` name and version `3.0.0a7`, and the seven tags
- `core/registry.py`, `server/`, `cloud_client/`, `tools/`
- `core/{atomic,cancellation,models,errors,branding}.py`
- all five import-linter contracts, the wheel exclusion, the CI and release
  workflows

What is brought across from the phase line, because `main` lacks it:

- `core/protocols.py` — see below, the one real conflict
- `core/config.py`, `core/consent.py` and their tests
- `tests/unit/test_models.py`, `test_protocols.py` — `main` had no tests for
  either
- the architecture, planning and ADR documentation system

### The one real conflict: `EngineStrategy.run()`

`main`'s published signature takes `progress: ProgressSink | None = None` and
**has no `cancellation` parameter at all**. The phase line's takes both, required.

The phase-line version wins, and this is the only place where "already released"
loses an argument. A strategy contract with no cancellation token means a tool
cannot be cancelled by its caller — which the HLD requires, and which no amount
of prior publication makes acceptable. `NullProgress` and `NEVER_CANCELLED`
exist precisely so the arguments can be required without burdening callers, and
requiring them deletes the `if progress is not None` branch from every future
engine rather than leaving a path that only runs in tests.

The blast radius is zero: every `run()` on `main` raises `NotImplementedError`.
Three signatures were updated — `tools/merge/local.py`, `tools/ocr/local.py`,
`tools/ocr/cloud.py` — and no behaviour changed, because there is none yet.

## Alternatives considered

**Keep the phase line as the base and merge `main` into it.** Rejected. It would
orphan seven published tags and a PyPI release, and discard CI and release
fixes that were validated against real runs the phase line has never had. The
phase line's unique content is four files and a documentation tree; `main`'s is
a released product.

**Keep `main`'s `protocols.py` and add cancellation later.** Rejected. "Later"
means after tools exist, at which point changing the signature is a real
migration rather than a no-op. Now is the cheapest this change will ever be.

**Amend ADR 0007 in place.** Rejected — ADRs are immutable. A decision that no
longer holds is superseded by a new one and both stay, so the reasoning that
turned out to be wrong remains legible.

**Rewrite history to make the lines converge.** Rejected outright. Tags are
published and the artifacts are on PyPI.

## Consequences

**Positive**

- One base, and it is the one users can actually install.
- The published `EngineStrategy` becomes cancellable before any tool implements
  it, which is the last moment that is free.
- `main` gains `config`, `consent`, and tests for `models` and `protocols` — it
  had none for the latter two.
- The documentation system lands on the line that is actually released, so
  `current-status.md` describes something a user can `pip install`.

**Negative — and accepted**

- **A published contract changed.** `EngineStrategy.run()` differs between
  `3.0.0a7` and whatever ships next. Defensible at alpha with no implementing
  tools, but it is a real break and belongs in the changelog.
- **ADR 0007 stands in the record as a decision made on a false premise.** That
  is the cost of immutable ADRs and it is the right cost — the alternative is a
  history that looks cleaner than it was.
- **`docs/planning/reconciliation.md` is now largely moot.** Kept for its
  evidence about `protocols.py`, marked as superseded, and deletable once that
  is no longer interesting.
- Four branches remain that are now redundant (`architecture`,
  `feat/core-foundation`, `phase-2/core-contracts`, `docs/architecture-and-planning`).
  None is deleted here; that is a separate decision.

## Enforcement

The mechanical part is already in place — `main`'s five import-linter contracts,
the wheel exclusion, and the hygiene suite all run in CI on every pull request,
and the reconciled branch passes them.

The process part is a lesson rather than a check: **fetch before deciding.** ADR
0007 would have been written differently after one `git fetch origin`. There is
no automated guard against reasoning from a stale remote, and inventing one
would be ceremony; the honest mitigation is that every future ADR touching
branch state names the commit it was written against, as this one does —
`origin/main` at `15b42c4`.

## Implementation impact

- **Code:** `core/protocols.py` replaced; `core/config.py` and `core/consent.py`
  added; three tool strategy signatures updated (signatures only — the stubs
  remain stubs).
- **Tests:** `test_config.py`, `test_consent.py`, `test_models.py`,
  `test_protocols.py` added; import-safety extended to probe each core submodule
  and to name `starlette`, `mcp`, and the cloud SDKs.
- **Docs:** `docs/architecture.md` moved to `docs/architecture/overview.md`
  keeping `main`'s better content; the planning and ADR system added; ADR 0007
  marked superseded; `reconciliation.md` marked overtaken.
- **Not touched:** `pyproject.toml`, `.importlinter`, CI workflows, `registry`,
  `server`, `cloud_client`, and every tag.
