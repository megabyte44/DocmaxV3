# Plans

Work that is decided but not yet written. Each file here is self-contained and
mergeable on its own, in the order below. Once a plan lands, delete it — the
decision it encodes lives in an ADR, and the enforcement lives in a test.

| # | Plan | Blocks | Size |
|---|---|---|---|
| 01 | [Spec-driven surfaces](01-spec-driven-surfaces.md) — one `ToolSpec` drives CLI, API, TUI, MCP | everything below | ~2 days |
| 02 | [Contract test suite](02-contract-test-suite.md) — one parametrized suite every tool inherits | tool #2 onward | ~1.5 days |
| 03 | [Stream targets](03-stream-targets.md) — keep `-` (stdin/stdout) representable | M9 pipelines | ~0.5 day (design only) |
| 04 | [JSON envelope](04-json-envelope.md) — `--json` on tool #1, not on tool #30 | M6 | ~0.5 day |
| 05 | [MCP pull-forward](05-mcp-pull-forward.md) — M10 becomes a thin adapter, ship it at M2 | — | ~1 day |
| 06 | [Doctor remedies](06-doctor-remedies.md) — per-platform install commands, registry-derived | M3 | ~0.5 day |

## Why this order

01 and 02 are the leverage. Both are cheapest to do while there is exactly one
tool in the tree and no CLI command has been hand-written yet — that condition
expires the moment M1 ships. 03 is a half-day of type design that prevents an
M9 rewrite. 04, 05, and 06 are each a small amount of work that gets
multiplied by every tool added after them, which is the only reason they are
scheduled early rather than at their roadmap milestone.

The common shape: **do the thing that makes tool #45 cost a fraction of tool
#1.** Nothing here adds a feature. All of it removes future work.

## Standing rules these produce

- [ADR 0006](../adr/0006-spec-driven-surfaces.md) — a tool is declared once;
  interfaces are generated from the declaration, never hand-written per tool.
- [ADR 0007](../adr/0007-contract-tests.md) — a tool's guarantees are verified
  by one suite parametrized over the registry, not by per-tool tests.

Both are enforced by hygiene tests, in the same way as ADR 0002 and ADR 0003.
See [CLAUDE.md](../../CLAUDE.md) for the working rules that follow from them.
