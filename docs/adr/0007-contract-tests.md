# ADR 0007 — A tool's guarantees are verified by one suite, not by its own tests

**Status:** Accepted · 2026-08-20

## Context

The README makes promises on behalf of every tool: inputs are never modified,
output is atomic, cancellation is always safe, nothing is overwritten by
accident, no operation ever shows a traceback. `docs/architecture.md` calls
these the structural guarantees and says they are "enforced by tests that run on
every commit — not by good intentions."

Today they are enforced for `core` and for nothing else. `tests/` holds five
hygiene tests and five unit tests over `atomic`, `cancellation`, `errors`,
`registry`, and the CLI. No tool has behavioural tests, because no tool has
behaviour yet.

The natural next step is per-tool test files, and it is the wrong one. Forty-odd
tools times fifteen guarantees is six hundred tests, nearly all of which are the
same test with a different `ToolSpec`. Written that way they will be copy-pasted,
they will diverge, and the tool most likely to skip them is the one written in a
hurry — which is also the one most likely to need them.

The hygiene suite already demonstrates the alternative: one AST walk,
parametrized over every source file, that no new file can escape.

## Decision

**The structural guarantees are verified by a single suite in `tests/contract/`,
parametrized over the registry. A tool enters the suite by registering.**

1. Each invariant is written once and runs against every registered tool.
   `tests/contract/` is the only place those assertions exist.
2. `tests/contract/samples.py` holds the arguments needed to exercise each tool,
   and `test_every_registered_tool_has_a_contract_sample` fails when a
   registered tool has no entry. **A tool cannot register without entering the
   suite.**
3. Tools whose `run()` is not yet implemented are listed in
   `NOT_YET_IMPLEMENTED` and skipped. `test_unimplemented_ledger_is_accurate`
   fails when a listed tool starts working, so the list can only shrink.
4. Fixture documents are **generated**, not committed. The corpus is described
   by the code that builds it, is byte-identical on every platform, and is
   created once per session.
5. Per-tool test files remain welcome for behaviour specific to that tool —
   that merge preserves bookmarks, that split honours page ranges. They must
   not re-test the guarantees.

The invariants are enumerated in `docs/plans/02-contract-test-suite.md` and, once
that plan lands, in the suite's own module docstring.

## Consequences

- Every future tool inherits ~15 behavioural tests on the day it registers, at
  the cost of one line in `samples.py`.
- The guarantees become falsifiable claims rather than documentation. Writing
  directly to a destination, mutating an input, leaking a staging file, opening
  a socket in a local engine, or ignoring a cancellation token each fail a named
  test that points at the ADR it violates.
- The suite is the specification. "What must a tool do?" has one answer, in one
  place, that executes.
- Cost: it runs on nine CI legs and will dominate wall-clock time if it is
  naive. `pytest-xdist` with `-n auto`, a session-scoped corpus, `-m "not slow"`
  by default, and a trimmed pull-request matrix keep it under a minute per leg.
  Revisit if it exceeds that.
- Cost: fault injection reaches into strategy internals to force a mid-write
  failure. That is a real coupling between the suite and the engine protocol,
  accepted because the alternative — trusting that fifty engines each clean up
  correctly on a path nobody exercises — is how v2's temp files ended up beside
  users' source documents.
- Cost: two ledgers to maintain. Both are self-enforcing: one fails when a tool
  is missing, the other fails when a tool starts working. Neither can rot
  quietly, which is the property that makes them worth having.
