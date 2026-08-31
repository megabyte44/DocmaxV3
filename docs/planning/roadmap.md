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
| **M8** | OCR, done properly | ✅ complete |
| **M9** | Pipelines, resumable batch, folder watch | ✅ complete — except `--resume`, [deferred](#what-m9-did-not-deliver) |
| **M10** | Local MCP server — drive DocMax from an AI agent | ✅ complete |
| **M11** | Remote MCP — a network-reachable tool server, for clients that can't spawn a local process | ✅ complete |

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

That ordering paid. `ocr` needed no new infrastructure at M8: it reuses the
router, the binary runner with its timeout and kill switch, the atomic writer,
the validator mechanism, the shared cloud flow, and the registry-generated TUI
form — none of which existed when the skeleton was written. What it did need was
a decision about pages that already carry text, and
[ADR 0022](../adr/0022-ocr-runs-tesseract-directly-and-skips-pages-that-have-text.md)
records it.

**M9 composes rather than adds.** Pipelines, batch and folder watch perform no
operation of their own: each stage goes through the same registry, the same
`EngineRouter` and the same validators as the equivalent single command, and a
bare `--tool` is a one-stage pipeline so there is one execution path rather than
three. That is why the milestone needed **no change to `core/`, the registry or
the router** — the same test M7 applied to the core contracts, applied again and
passed again. See [ADR 0023](../adr/0023-runners-are-a-package-below-the-interfaces.md).

## What M9 did not deliver

**`--resume` does not exist**, and the row above says "resumable batch". A
resume key needs a journal: a persistent, app-owned file with a location, a
schema version, and a defined behaviour when it is corrupt or from a future
version. [ADR 0008](../adr/0008-consent-record.md) settled exactly that shape for
the consent record *before* the code that used it, and a journal deserves the
same treatment rather than a format improvised alongside three other features.

It was deferred rather than invented, which leaves one visible cost: re-running
an interrupted batch repeats the documents that already succeeded. That is safe
— their destinations exist, and `OutputTarget` refuses them without `--force` —
but it is not what the word "resumable" promises.

The same deferral applies to the watcher, whose processed-file set lives in
memory and does not survive a restart.

**M10 is the fourth driver of one core, and the argument closes here.** The
architecture has claimed since M0 that `ToolSpec` plus `EngineRouter` could serve
a CLI, a TUI, an HTTP server and an MCP server without modification. M7 tested
that and passed, M9 tested it again and passed, and M10 — the interface with the
most reason to want a new field, a new execution model and a new error shape —
needed **no change to `core/`, the registry or the router** either. Three
milestones, one core, no amendments.

What M10 did need was a *policy* layer, and that is not a Core change: an MCP
client is a program acting on someone's behalf, so reads and writes are confined
to `--root`, cloud engines are off unless asked for, and an agent cannot consent
to an upload on the user's behalf. See
[ADR 0029](../adr/0029-the-mcp-policy-boundary.md).

## M11 — why it isn't just M10 with a different transport

M10's tool schema takes `inputs` as **local filesystem paths**, checked against
`--root` (ADR 0029). That only means something when the client and server share
a filesystem — true over stdio, false over a network. A remote client (ChatGPT's
connectors, or any MCP-over-HTTP client) has no local path to hand the server, so
this is not "mount the same server on a port":

- **The tool contract changes.** `inputs` becomes a reference to something
  already uploaded, not a path — the shape `docmax.server` already chose for the
  same problem (`POST /uploads` → `file_id` → job references it). Whether M11
  reuses that job model or defines its own is an open question.
- **The policy boundary changes.** ADR 0029 assumes one trusted local caller.
  A network-reachable MCP session needs an auth model — who may open one, and
  whether "roots" mean anything once the caller isn't on the same machine.
- **It may not be a new interface at all.** `docmax.server` is already
  network-reachable (ADR 0006); M11 could be a transport bridge in front of it
  rather than a fourth thing. That choice belongs in the ADR, not this table.

None of this was decided when this section was written.
[ADR 0035](../adr/0035-remote-mcp-is-a-transport-bridge-over-the-cloud-server.md)
settled it — a route inside `docmax.server`
(`POST /v1/mcp`), not a fourth thing, reusing the existing upload/`file_id`/job
model and bearer-token auth. See
[phases.md](phases.md#phase-11--remote-mcp-transport-m11) for what shipped and
[backlog.md](backlog.md#decisions-owed-an-adr) — the ADR resolved this item
there too.

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
