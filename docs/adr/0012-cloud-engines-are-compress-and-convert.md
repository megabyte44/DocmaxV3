# ADR 0012 — M6 ships two cloud engines, and a cloud run never falls back to local

**Status:** Accepted · 2026-08-27

## Context

`architecture/overview.md` and `architecture/layers.md` have both said the same
thing since M0:

> Only five tools have a cloud engine — `ocr`, `compress`, `convert`, `pdfa`,
> `remove-bg` — because cloud exists to eliminate install pain and nothing else.

Auditing that list against the registry before starting M6 found that it
describes an end state, not a buildable milestone:

| Tool | Local engine | Can it have a cloud engine at M6? |
|---|---|---|
| `compress` | done, M3 (Ghostscript) | **yes** |
| `convert` | done, M5 (Pandoc) | **yes** |
| `ocr` | skeleton — `run()` raises `NotImplementedError` | no; the roadmap gives OCR its own milestone, M8 |
| `pdfa` | **does not exist** | no |
| `remove-bg` | **does not exist** | no |

`pdfa` appears in the codebase exactly once, as a string in `_binaries.py`'s
`used_by=("compress", "pdfa")`. `remove-bg` appears in no source file at all.
Neither is on any roadmap row.

The reason this matters more than "two of five is fewer than five": the whole
argument for building cloud at M6 rather than M2 is `roadmap.md`'s — *"Building
the remote half first would mean specifying a contract for operations nobody has
implemented, then finding out at M8 that the contract was wrong."* A cloud engine
for a tool with no local engine is exactly the thing that ordering forbids.

Separately, the cloud path raises a question the local path never has to answer:
what happens when the chosen engine fails half way through.

## Decision

**M6 ships cloud engines for `compress` and `convert`. Nothing else.**

Those two are the tools whose local engines exist *and* whose local
dependencies are genuinely painful — Ghostscript, and Pandoc plus a LaTeX
distribution. They are the two the architecture's own justification for cloud
actually describes.

`ocr` keeps its `cloud.py` skeleton and its `Engine.CLOUD` declaration
untouched. It is the reference shape the two new strategies are written from,
and M8 fills in its `run()` alongside the local engine. Adding a working cloud
OCR at M6 would mean shipping OCR two milestones early through the back door.

`pdfa` and `remove-bg` are not built, not stubbed, and not declared. A tool
that does not exist does not get an engine.

**A cloud run that fails does not silently re-run locally.**
`EngineRouter.resolve()` picks one engine *before* any work starts, and the
result reports which one ran. A failure after that point is reported as a
failure of the engine that was chosen.

**The consent and offline gates stay exactly where they are.** No new gate, no
second check, no bypass. `offline = true` makes cloud unreachable regardless of
`--engine cloud`; a missing per-tool grant raises `ConsentRequiredError`; both
are enforced by `EngineRouter` before a strategy is constructed, which is what
makes "no document is uploaded without consent" a property of one function
rather than of three.

## Alternatives considered

**Declare all five and let the ones without local engines fail at run time.**
It would make `GET /v1/capabilities` match the example in `cloud-api.md`.
Rejected: the router would offer an engine that cannot run, which is exactly
what `compress/tool.py`'s M3 comment warned against — *"declaring it before it
does would make the router offer something that cannot run."* Consistency with
an aspirational document is not worth a broken routing decision.

**Include `ocr` because its cloud strategy is nearly written.** Tempting: the
skeleton is real, and the endpoint would do the work. Rejected on the roadmap's
own reasoning — OCR is *"the operation v2 got most wrong"* and is deliberately
late so it benefits from a mature router and a proven validator mechanism. A
cloud OCR shipping first would mean the local one is written afterwards against
a contract chosen without it, which is the failure M8's placement exists to
avoid.

**Automatic fallback to local when cloud fails.** Superficially friendly. Three
reasons it is not: it doubles the work for a document the user has already paid
to upload; it makes `engine_used` a guess rather than a fact; and it converts a
loud, actionable failure (`cloud.quota_exceeded`, with a remedy) into a silent
slow path that the user discovers from a bill or a stopwatch. The user can
retype the command with `--engine local` in two seconds, knowing why.

**Fallback behind an opt-in flag.** Rejected for M6 as speculative — nobody has
asked for it, and it is addable later without breaking anything. If it ever
lands, `ToolResult` needs a way to say "this is the second engine I tried",
which is a change worth making deliberately.

## Consequences

**What it costs.**

- `architecture/overview.md` and `layers.md` name five tools and DocMax will
  ship two. Both documents are describing the end state and are now visibly
  ahead of the code — they are amended to say so rather than left to read as a
  description of the present.
- `GET /v1/capabilities` will report `["compress", "convert", "ocr"]` — `ocr`
  because its spec declares `Engine.CLOUD` and the route derives from the
  registry — while only two of those can actually run. That is a real wart. It
  is accepted rather than fixed by un-declaring `ocr`, because un-declaring it
  would delete the skeleton M8 is meant to fill in.
- A user whose cloud job fails must retype the command. That is the intended
  cost.

**What it buys.** Every cloud engine M6 ships is one whose local counterpart is
implemented and tested, so the wire contract is specified against operations
that exist. And `engine_used` in a `ToolResult` remains a statement of fact.

## Enforcement

- A test asserts that the set of tools declaring `Engine.CLOUD` **and** having a
  working cloud `run()` is exactly `{compress, convert}` — so adding a third by
  accident fails, and so does quietly implementing `ocr`'s.
- A test asserts `pdfa` and `remove-bg` are absent from the registry, which
  turns "someone added the tool the docs mention" into a deliberate act.
- The existing consent gate gains the test [phases.md](../planning/phases.md)
  named as Phase 5's definition of done and which has never been written:
  **no path reaches `cloud_client` without passing a consent check.** Asserted
  by driving every cloud-capable tool through the router with an empty consent
  store and a transport that fails the test if it is touched.
- A test asserts `offline = true` defeats an explicit `--engine cloud`.
- No test enforces "no fallback", because it is the absence of code. The
  `engine_used` assertions are the closest thing, and this section says so
  rather than implying coverage that does not exist.
