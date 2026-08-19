# Plans

Work that is decided but not yet written. Each file here is self-contained and
mergeable on its own. Once a plan lands, delete it — the decision it encodes
lives in an ADR, and the enforcement lives in a test.

Not to be confused with [`docs/planning/`](../planning/), which tracks *where
the project is*. This directory holds *what to build next and why*.

| # | Plan | State | Size |
|---|---|---|---|
| 00 | [Progress](00-progress.md) — branch state, what the M1–M3 stack changed, where to start | — | read first |
| 01 | [Spec-driven surfaces](01-spec-driven-surfaces.md) — one `ToolSpec` drives CLI, API, TUI, MCP | partly built: router ✅, generation ❌ | ~2 days |
| 02 | [Contract test suite](02-contract-test-suite.md) — one parametrized suite every tool inherits | not started | ~1.5 days |
| 03 | [Stream targets](03-stream-targets.md) — keep `-` (stdin/stdout) representable | not started | ~0.5 day (design only) |
| 04 | [JSON envelope](04-json-envelope.md) — `--json` now, not at M6 | not started | ~0.5 day |
| 05 | [MCP pull-forward](05-mcp-pull-forward.md) — M10 becomes a thin adapter | not started | ~1 day |
| 06 | [Doctor remedies](06-doctor-remedies.md) — per-platform install commands | **mostly built** by PR #10 | ~0.5 day left |

## Why this order

All six have the same shape: **do the thing that makes tool #45 cost a fraction
of tool #1.** None adds a feature. All of them remove future work.

01 and 02 are the leverage, and 01 is now the urgent one. It was written when
the CLI had no per-tool commands at all; the M1–M3 stack then landed nine
hand-written ones. Nine is still a cheap refactor. Forty is a rewrite nobody
ever schedules.

02 got *more* valuable for the same reason. A contract suite written before any
tool exists proves nothing on day one. Written across ten independently
authored tools, it is a bug hunt.

03 is half a day of type design that prevents an M9 rewrite, and it now costs
ten engines instead of one — it gets more expensive every milestone it waits.

## Revised after PR #10

These plans were written against `main` at `3.0.0a7`, where `merge` and `ocr`
were stubs. The M1–M3 stack then landed the router, the CLI wiring, and ten
working tools. Every plan has been reconciled against that; 01 and 06 changed
substantially, and both changes were in the project's favour.

Two places where the stack's design beat the plan, recorded rather than
quietly overwritten:

- **The binary table** should stay central. It declares tools that do not exist
  yet, so `doctor` can report on the whole roadmap. Plan 06 §2, plan 01 R1.
- **`Binary.commands` is a tuple**, because Ghostscript's console build is
  `gswin64c` on Windows and bare `gswin64` opens a window that never returns.

## Standing rules these produce

- [ADR 0010](../adr/0010-spec-driven-surfaces.md) — a tool is declared once;
  interfaces are generated from the declaration, never hand-written per tool.
- [ADR 0011](../adr/0011-contract-tests.md) — a tool's guarantees are verified
  by one suite parametrized over the registry, not by per-tool tests.

Both are enforced by hygiene tests, in the same way as ADR 0002 and ADR 0003.
See [CLAUDE.md](../../CLAUDE.md) for the working rules that follow from them.
