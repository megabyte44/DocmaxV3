# ADR 0023 — Pipelines, batch and watch live in one package below the interfaces

**Status:** Accepted · 2026-08-28

## Context

M9 adds three things that compose existing operations rather than performing
new ones: a pipeline (several tools over one document), a batch (one operation
over many documents), and a folder watch (a batch that never ends).

None of them is a tool. None belongs to a tool. All three need the same four
things — the registry, `EngineRouter`, `OutputTarget` and `CancellationToken` —
and all three are things a second interface will eventually want. `docmax batch`
is obvious; a TUI batch screen and an M10 MCP `run_pipeline` are both plausible,
and neither may import the CLI.

That last point is the constraint. `interfaces-are-independent` forbids
`docmax.tui -> docmax.cli` outright. If the batch loop lives in `cli/`, then the
TUI cannot have batch without either duplicating the loop or breaking the
contract. M7 met this exact problem with the pickers and answered it by giving
them their own package — [ADR 0019](0019-picker-package-and-rendering.md).

The alternative reading is that this is orchestration, and orchestration is
already in `core` — `EngineRouter` lives there. That is the argument for
`core/batch.py`, and it is not a weak one.

## Decision

**M9's three runners live in a new package, `docmax.runners`, on its own layer
between `docmax.pickers` and `docmax.tools`.** It is library code: it never
prints, never exits, and never writes a destination itself.

```
docmax.cli
docmax.tui
docmax.server
docmax.pickers
docmax.runners      <- new
docmax.tools
docmax.cloud_client
docmax.core
```

It imports `docmax.core` and nothing else in the project. In particular it does
not import `docmax.tools`: a runner names a tool by string and the registry
resolves it, exactly as the CLI does. The layer sits above `tools` only because
the contract must be totally ordered and something has to go first.

**Core is unchanged by M9.** No new field on `ToolSpec`, no new method on
`EngineRouter`, no change to `OutputTarget`, `atomic.py`, `ProgressSink` or
`CancellationToken`. That is the load-bearing claim of this ADR and the reason
the package sits outside `core` rather than inside it.

## Alternatives considered

**`core/pipeline.py`, `core/batch.py`, `core/watch.py`.** Defensible — the
router is already core orchestration. Rejected because it inverts what `core`
has meant for nine milestones: `core` holds the contracts every layer speaks,
and every module in it is a thing a tool or an interface *uses*. A pipeline is a
product feature that composes those contracts. Putting it in `core` would mean
`import docmax.core` brought a folder watcher with it, and would make the
"core imports nothing heavy and knows about no feature" line harder to hold at
M10. It also makes the M9 blast radius look larger than it is: a reviewer
reading `git diff core/` should see nothing, and with this decision they do.

**`cli/batch.py` and friends.** Smallest diff, and wrong for the reason above:
it puts a reusable loop behind a contract that forbids the second user from
reaching it. This is the mistake ADR 0019 already declined to make.

**Three separate packages.** `docmax.pipeline`, `docmax.batch`, `docmax.watch`.
Rejected: they share the execution unit. Batch runs a pipeline over many inputs
and watch runs a batch forever, so splitting them would mean three packages with
two import edges between them, which is one package with extra steps.

**A `Runner` protocol in `core/protocols.py`.** Speculative. There is one
implementation of each of the three, and a protocol with no second implementer
is indirection rather than architecture — the same reasoning Phase 2 used to
leave `JobStatus` and the storage protocols out until they had a caller.

## Consequences

- The layers contract gains a line, and `tests/paths.py` gains `"runners"` in
  `LIBRARY_PACKAGES`. A rule and its enforcement land together, as always.
- Being in `LIBRARY_PACKAGES` means the no-direct-writes and no-sys-exit hygiene
  tests cover the runners. A batch loop that called `sys.exit` on a failed item
  would kill the other 199, which is *precisely* v2's defect that
  `test_no_sys_exit.py` was written for; now it is a build failure there too.
- A runner cannot report progress or errors by printing. It takes a
  `ProgressSink` and returns outcomes; the CLI renders them. That is more code
  than a `print` and it is what makes the same loop usable from the TUI.
- The layer is above `tools`, so nothing stops a future runner from importing a
  tool directly. Only convention and review prevent it — the layers contract
  cannot express "may not import the layer below". Named here because it is a
  real gap rather than a covered one.
- One more total-order entry that does not mean what a casual reader thinks.
  `pickers` above `runners` says nothing about either; they never meet.

## Enforcement

- `.importlinter`, `layers` contract — the `docmax.runners` line. A runner
  importing `docmax.cli`, `docmax.tui` or `docmax.server` fails `lint-imports`.
- `.importlinter`, `core-is-standalone` — `docmax.runners` is added to the
  modules `docmax.core` may not import, so the "core is unchanged and unaware"
  claim above is checked rather than promised.
- `tests/paths.py` — `"runners"` in `LIBRARY_PACKAGES`, which enrols the package
  in `test_no_direct_writes.py` and `test_no_sys_exit.py`.
- `tests/unit/test_m9_runners.py::test_runners_import_only_core` — an AST scan
  asserting no `docmax.tools` import, which the layers contract permits and this
  ADR forbids.
