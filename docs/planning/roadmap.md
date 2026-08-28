# Roadmap

The product view: what a user can do, in the order it ships. For the engineering
order underneath it, see [phases.md](phases.md). For what is true right now, see
[current-status.md](current-status.md).

The README carries a summary of this table. This file is the canonical version.

| Milestone | Delivers | Status |
|---|---|---|
| **M0** | Foundation — architecture, CI, safety mechanisms | ✅ complete |
| **M1** | Core engine + `merge` as the reference implementation | ✅ complete |
| **M2** | `split`, `rotate`, `reorder`, `pages`, `metadata`, `sanitize`, `get-info` | ✅ complete |
| **M3** | `compress` + external-binary support in `doctor` | ✅ complete |
| **M4** | `watermark`, `stamp`, `protect`, `unlock`, `permissions` | ✅ complete |
| **M5** | `convert`, `to-images`, `from-images` | ✅ complete |
| **M6** | Cloud engines, `--json` everywhere, published benchmarks | ✅ complete |
| **M7** | Textual TUI + visual pickers for crop and reorder | ✅ complete |
| **M8** | OCR, done properly | planned |
| **M9** | Pipelines, resumable batch, folder watch | planned |
| **M10** | Local MCP server — drive DocMax from an AI agent | planned |

## What the ordering is for

The sequence is not arbitrary, and two choices in it are worth defending.

**M1 delivers one tool, not several.** `merge` is the reference implementation:
the first tool to go through the registry, the router, `OutputTarget`, atomic
writes and the validator mechanism end to end. Every tool after it is a copy of a
proven shape. Building three tools at once would mean discovering the shape three
times and reconciling the results.

**Cloud engines are M6, not M2.** The Cloud Engine exists so a user can skip
installing Tesseract or a LaTeX distribution — which is only meaningful once
those local engines exist to be skipped. Building the remote half first would
mean specifying a contract for operations nobody has implemented, then finding
out at M8 that the contract was wrong.

**M7 had to build a tool to keep a promise.** The row says "pickers for crop
and reorder", and `reorder` existed while `crop` did not —
[ADR 0005](../adr/0005-gui-pickers.md) had assumed the headless flags shipped at
M2, and they did not. Since that ADR's rule is that the headless form ships
first, M7 built `crop --box` before `crop --interactive`. `redact`, the third
picker ADR 0005 names, is on no roadmap row and has no headless form; it was not
built.

**OCR is M8, deliberately late.** It is the operation v2 got most wrong, it has
the heaviest dependencies, and it benefits most from a mature router and a proven
validator mechanism. Doing it early would mean doing it twice.

## What is not on the roadmap

Stated because their absence is a decision, not an oversight:

- **A browser UI.** The premise is that the good self-hosted options all assume a
  browser, and DocMax assumes a terminal instead. An HTTP server exists at M6 to
  serve the Cloud Engine, not to serve a web app.
- **A paid capability tier.** Every document operation is free and permissively
  licensed, forever — see [ADR 0004](../adr/0004-open-core-boundary.md). What is
  gated is team- and scale-shaped, never capability-shaped.
- **Broad format support before correctness.** v2 supported more formats than v3
  currently does, and produced files that nothing could open. The order here
  favours a small set that works.

## Benchmarks

Published in `benchmarks/` with real hardware and methodology, at M6. **No
numbers appear in the README or anywhere else until they are measured** — a
performance claim without a method behind it is marketing.

The harness landed at M6: `python -m benchmarks`, two tools, four generated
fixtures, one warmup and five timed runs reported as median and minimum. See
[benchmarks/METHODOLOGY.md](../../benchmarks/METHODOLOGY.md).

**There are still no published numbers.** The development machine has neither
Ghostscript nor Pandoc, so the one committed results file records that every row
was skipped and why. That is the rule working, not the rule being broken.
