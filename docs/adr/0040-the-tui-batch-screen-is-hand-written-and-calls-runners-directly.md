# ADR 0040 — The TUI's batch screen is hand-written, and calls `runners.batch` directly

**Status:** Accepted · 2026-09-10

## Context

`docmax batch --tool NAME inputs... --output-dir DIR` has existed since M9:
one tool, run over many documents, each producing its own output — backed by
`docmax.runners.batch.run_batch` (ADR 0025). The TUI had no equivalent. A
user who wanted to compress two hundred scanned PDFs one at a time had to
leave the TUI for the CLI to do it in one command.

ADR 0023, which put `pipeline`/`batch`/`watch` in their own package below the
interfaces rather than inside `cli/`, said exactly why this gap should not be
permanent:

> `docmax batch` is obvious; a TUI batch screen and an M10 MCP `run_pipeline`
> are both plausible, and neither may import the CLI.
> — ADR 0023

`.importlinter`'s layers contract already places `docmax.runners` below
`docmax.tui` (`core < cloud_client < tools < runners < pickers < mcpschema <
mcp < server < tui < cli`), so the TUI could reach `run_batch` without a new
import edge. Nothing was blocking this except that nobody had built the
screen — the gap was inertia, not a rule.

The one real question was where a batch screen fits into ADR 0021's "the TUI
is generated from `ToolSpec`, and names no tool." `pipeline`, `batch` and
`watch` were deliberately never registered as tools (`tests/unit/test_tui.py
::test_the_tui_offers_exactly_what_the_cli_exposes`'s docstring: *"M9's
`pipeline`, `batch` and `watch` compose tools rather than being any"*), so
there is no `ToolSpec` for a batch screen's form to be generated from in the
way `RunScreen` generates one per tool. This is the same situation
`HelpScreen`, `SystemCheckScreen` and `CloudStatusScreen` were already in
(GitHub #39): each is hand-written, because each answers a question `core`
has no `ToolSpec` shape for.

A second, narrower question was where in the TUI's navigation a batch screen
belongs. `MenuScreen`'s own docstring scopes it to screens that "do no work
of its own" — help text, a status readout, a yes/no modal. A batch run starts
a worker thread, writes files, and can be cancelled mid-run: the same shape
opening a tool from `ToolListScreen` already has, not the shape `MenuScreen`
was built for.

## Decision

**A new screen, `BatchScreen`, hand-written in `tui/app.py` alongside
`RunScreen`.** It is reached by its own binding and button on
`ToolListScreen` (`b`, and a "Batch" entry above the tool list), not folded
into `MenuScreen` — for the reason above: it does work, the same category as
opening a tool, not the category `MenuScreen` exists for.

It renders:

- a tool `Select`, populated from a new `catalog.batchable_tools()` —
  `offered_tools()` filtered to `spec.produces_output`, since a report-only
  tool (`get-info`, `permissions`) has nothing to mirror into an output
  directory, the same field `RunScreen` already reads to skip its own output
  field for those two tools (ADR 0036);
- inputs, always via a multi-select native dialog (`browser.pick_files
  (multiple=True)`) — batch is unconditionally many-in, unlike `RunScreen`
  where `multiple` depends on `ToolSpec.accepts_multiple_inputs`;
- an output directory, via a new `browser.pick_directory()` (a thin wrapper
  around `tkinter.filedialog.askdirectory`, the same shape as the existing
  `pick_files`/`pick_save_path`);
- the chosen tool's own parameters, rendered by the same `forms.fields_for`/
  `forms.collect` every generated form uses — rebuilt whenever the tool
  dropdown changes, via a small child widget (`_BatchParamsPanel`) recomposed
  with `refresh(recompose=True)` rather than the whole screen, so switching
  tools does not discard the input/output paths already browsed to;
- engine, overwrite and dry-run controls, identical in shape to `RunScreen`'s;
- a live results table, one row per finished document, appended as
  `run_batch`'s `on_outcome` callback fires — the same callback the CLI's own
  `_print_outcome` consumes, marshalled to the UI thread exactly as every
  other callback in this file is.

Running calls `docmax.runners.batch.run_batch` and
`docmax.runners.pipeline.single_stage` directly — the pre-built `tui ->
runners` edge — after building a router with `runner.build_router()`, the
same four-line construction `RunScreen` already duplicates rather than
importing from `cli/execution.py` (peers do not import each other). A
whole-batch failure (`plan_batch`'s missing-directory or colliding-
destination checks, `refuse_unnameable_output` for a tool like `convert`) is
a `DocMaxError` raised before any item runs, and is shown through the
existing `ErrorScreen` — no batch-specific error UI.

**Scope is `--tool` only.** `BatchScreen` builds a one-stage `Pipeline` via
`single_stage`, the same object `batch --tool` builds on the CLI. It does not
offer a `--pipeline` file, and there is no TUI screen for `watch`. Both are
open extensions of this exact pattern — a hand-written screen calling
`runners` directly — not ruled out by this decision, just not asked for yet.

## Alternatives considered

**Add `implemented`/`batchable`-style fields to `ToolSpec` so a batch screen
could be "generated" too.** Rejected for the same reason ADR 0021 gave for
not doing this for the report-only and directory-output cases before
`produces_output`/`produces_directory` existed: there is no per-tool fact to
add here. What varies across a batch run is not the tool's shape, it is
*how many documents* and *where they land* — a fact about the run, not the
tool. `produces_output` is reused as-is, unchanged.

**Fold the batch screen into `MenuScreen`.** Rejected per the Context above:
`MenuScreen` is scoped to screens with no side effects, and a batch run has
plenty.

**Give the CLI's `_render_report` a shared helper the TUI also calls.**
Rejected: `_render_report` prints to a Rich console and raises `typer.Exit`,
the same reasons `cli/execution.py::execute` is not reused by `tui/runner.py`
(the module's own docstring: *"cli and tui are peers"*). `BatchScreen` reads
the same `BatchReport`/`ItemOutcome` shapes and renders them into Textual
widgets instead, duplicating the small amount of formatting logic
(`_append_outcome`'s "code: message" line mirrors `_print_outcome`'s), not
the framework-specific rendering itself.

## Consequences

- A tool can now be run over many documents from the TUI, with per-document
  results and a cancel button, with no CLI round-trip.
- `docs/planning/current-status.md`'s "the M9 runners are not exposed" note
  narrows: `batch` is now exposed via the TUI; `pipeline` files and `watch`
  remain unexposed there, named as the open extensions above.
- Two small, generic helpers used only by one tool form before —
  `_render_field`, `_render_group`, `_apply_mode_change`, `_value_of` and
  `_composite_value_of` — moved from `RunScreen` methods to module-level
  functions in `tui/app.py`, since `BatchScreen` needed the identical
  rendering for whichever tool's parameters are currently selected. No
  behaviour changed; `RunScreen` calls the same functions it always did.
- `tests/unit/test_tui.py::test_the_tui_offers_exactly_what_the_cli_exposes`
  needed no change: `batch` still names no `ToolSpec`, so it correctly stays
  in that test's fixed exception set. Its docstring was updated to say why —
  a hand-written screen, not a generated one — rather than implying batch has
  no TUI presence at all.

## Enforcement

- `tests/unit/test_tui_batch.py` — tool selection reads `catalog
  .batchable_tools()` and excludes report-only tools; choosing a different
  tool rebuilds the params panel; Browse always asks for multiple inputs; the
  output-directory dialog remembers the last folder end to end; a run calls
  `run_batch` with the gathered arguments and renders one row per outcome
  plus the summary; a cancelled run cancels the token; a whole-batch
  pre-flight error shows `ErrorScreen`; Run is refused before any worker
  starts when the form is incomplete.
- `tests/unit/test_tui.py` — `test_pressing_b_opens_the_batch_screen` and
  `test_clicking_batch_in_the_sidebar_opens_the_batch_screen` cover the entry
  point; the existing whole-package AST scans
  (`test_the_tui_names_no_tool_except_the_unimplemented_one`,
  `test_the_tui_imports_no_other_interface`) already walk
  `src/docmax/tui/app.py` and so cover `BatchScreen` with no edit of their
  own — it names no tool by literal and imports nothing from `cli`/`server`/
  `mcp`.
- `tests/unit/test_tui.py`'s `pick_directory`/`_native_directory_dialog`
  tests mirror the existing `pick_save_path`/`_native_save_dialog` ones.
