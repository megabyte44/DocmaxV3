# ADR 0007 — `m1-foundations` is a source branch, absorbed phase by phase

**Status:** Accepted · 2026-08-15

## Context

Two independent lines of work exist over milestone M1. They fork cleanly at
`4fc92f2` (M0) and share no commit:

```
                    ┌── 06b1e54 ── cfb4ec7 ── 8ba97e2   architecture
4fc92f2 (M0) ───────┤
                    └── be3e3b6 ── … ── a0c3e52          m1-foundations
```

`architecture` carries Phases 0–2: the documentation system and the Core
contracts, verified under the full toolchain.

`m1-foundations` carries an earlier, broader attempt at the whole of M1 — a tool
registry, a cloud client, a reference API server, and two tool skeletons — plus
its own copy of the four Core modules Phase 2 later rebuilt.

The branch was briefly believed lost, then recovered and pushed. Its existence
is not a mistake to be undone: it contains roughly 2,000 lines of work that
Phases 4, 6, 7 and 8 would otherwise write from scratch. But it also contains a
second `core`, and two divergent `core` implementations in one repository is
precisely the drift the architecture documentation exists to prevent.

A decision was owed before Phase 4, because Phase 4 builds a registry — and
`m1-foundations` already has one.

## Decision

**`m1-foundations` is preserved as a read-only source branch. Its components are
ported into `architecture` by the phase that owns them, never by a merge.**

Concretely:

- **No merge, now or later.** The branch is never merged into `architecture`.
- **No deletion.** It stays on `origin` as the reference copy.
- **No further commits on it.** It is a source, not a development line. Work
  continues on `architecture` only.
- **Its Core copies are discarded**, not merged. `architecture` already has the
  same contracts, in a better state.
- **Its unique work is adopted by phase**: registry at Phase 4, tools at Phase 6,
  cloud client at Phase 7, server and enforcement at Phase 8.

The per-component evidence and disposition table is in
[planning/reconciliation.md](../planning/reconciliation.md). That document is the
working record; this ADR is the decision.

## Alternatives considered

**A — Merge `m1-foundations` into `architecture`.** Rejected, for three reasons
that compound:

1. It would conflict in all four overlapping Core modules, and the natural
   resolution — take the incoming version — **silently regresses `protocols.py`**.
   M1's `EngineStrategy.run()` takes `progress` as optional and has no
   `cancellation` parameter at all. Phase 2 made both required precisely so that
   no engine carries `if progress is not None` and every engine is cancellable.
   A merge conflict is a bad place to re-litigate that.
2. It would land Phases 4, 6, 7 and 8 in a single commit, none of it verified,
   destroying the phase discipline that makes the work reviewable.
3. Its Core copies **fail the current toolchain**. Measured, not assumed: `ruff`
   reports the same ten errors in M1's `atomic.py` and `cancellation.py` that
   Phase 2 has already fixed — four `PTH105`, four `RUF100`, one `SIM105`, one
   `S110`. Merging re-introduces every one of them.

**B — Cherry-pick commits.** Rejected as the *mechanism*, though the spirit is
right. M1's commits interleave Core with the components built on it —
`be3e3b6` contains the registry *and* models *and* protocols — so no commit can
be taken without also taking a Core copy that is to be discarded. Porting at
file granularity is what the content actually permits.

**D — Supersede M1 entirely and rewrite.** Rejected as waste. The registry is
complete, ADR 0002-compliant, and lint-clean; the server is a working
implementation of the contract in `cloud-api.md`. Rewriting them from memory
would produce something worse at greater cost.

## Consequences

**Positive**

- Nothing is lost, and nothing is duplicated. Each component enters the codebase
  once, at the phase that can verify it.
- **The enforcement debt is already paid.** `m1-foundations` implements every
  item the Phase 2 backlog lists as "not yet enforced" — the `docmax.server`
  layer, `fastapi`/`mcp` in the forbidden list, an `interfaces-are-independent`
  contract, a `server-is-not-a-client` contract, the wheel exclusion with its
  hygiene test, and the `server` extra. Phase 8 inherits them rather than
  writing them.
- **[ADR 0006](0006-reference-server-location.md) is corroborated.** It reasoned
  that the server belongs in-tree, open, and excluded from the wheel. M1 had
  already built exactly that, independently. The decision was not merely
  defensible in the abstract; it is what the working implementation does.

**Negative — and accepted**

- **A permanently divergent branch on the remote.** Anyone browsing the
  repository sees two implementations and must be told which is live. Mitigated
  by this ADR, the reconciliation document, and a note in the status page — but
  it is real, and the cost persists until the branch is fully absorbed.
- **Porting is manual.** Each component must be re-read against the current Core
  before it lands. That is the work; the branch is a draft, not a delivery.
- **The branch decays.** Every Phase-2-era change makes M1's copies staler. This
  is bounded — it only matters for components not yet ported — but it argues for
  absorbing the registry sooner rather than later.

## Implementation impact

- **Code:** none. This ADR changes no source file. Both branches are untouched.
- **Docs:** adds `planning/reconciliation.md`; updates `phases.md` (Phases 4, 6,
  7, 8 gain a "port from `m1-foundations`" input), `current-status.md`,
  `backlog.md`, and the [ADR index](README.md).
- **Phases:** unchanged in order and scope. Phase 3 remains next.

## Enforcement

Mostly social, and stated plainly because of it:

- **The branch must not receive new commits.** Nothing mechanical prevents this;
  if it happens, the two lines diverge again and this ADR is void.
- **A component is ported once.** When a phase adopts one, it strikes it from
  the table in `reconciliation.md`, so what remains outstanding is always
  visible.
- **`reconciliation.md` is deleted when the table empties**, and this ADR is then
  marked superseded by nothing — simply historical. A reconciliation document
  that outlives the reconciliation is exactly the stale planning artefact the
  documentation system is meant to avoid.

The mechanical checks that *do* exist are the ones already in CI: any ported code
must pass `ruff`, `mypy --strict` and `lint-imports` before it lands, which is
what catches a stale Core copy arriving by accident.
