# ADR 0021 — The TUI is generated from `ToolSpec`, and names no tool

**Status:** Accepted · 2026-08-28

## Context

A TUI is where a document toolkit usually acquires a second implementation of
itself. v2 is the worked example, and
[ADR 0002](0002-registry-mechanism.md) opens with it:

> v2 dispatched its interactive menu through a hardcoded `if/elif` chain over
> [tool names] … one entry had been renamed, raising `ImportError` and killing
> the whole interactive session.

The registry was built so that could not recur. `Param`'s docstring has said
since M0 what the TUI's share of it is:

> The CLI turns these into options, **the TUI into form fields**, the API server
> into request validation, and the M10 MCP server into a JSON schema.

and `Param.type_` is *"deliberately a small closed set, since every consumer has
to be able to render it"*.

So the mechanism was ready. What was not decided is whether M7 would use it, and
that decision is worth pinning down, because "just this one tool needs a custom
screen" is how the erosion starts and it always looks reasonable the first time.

## Decision

**There is no per-tool code anywhere in `docmax.tui`.**

One `RunScreen` serves all eighteen tools. It reads a `ToolSpec` from the
registry, turns each `Param` into a field through `tui/forms.py`, and hands the
collected values to `EngineRouter.run` as `**params`. Adding tool nineteen adds
a row to the list and a form, with no edit to this package.

`tui/forms.py` holds the whole mapping and imports `textual` nowhere, so the
interesting half — which fields exist, what they default to, whether `"3"` is a
valid page count — is testable with no terminal, no screen size and no event
loop. The widgets are then thin enough that a handful of `Pilot` tests prove
only that they are wired up.

**One tool name is allowed to appear, and it is a documented exception.**

`ocr` is registered with a complete `ToolSpec` — both engines, three parameters
— and a `run()` that raises `NotImplementedError` until M8. The CLI never
exposed it, so nobody has met that. A TUI listing the registry verbatim *would*
offer it, and choosing it would produce an `InternalError` wrapping a
`NotImplementedError`: a traceback-class failure for a condition known in
advance, which is exactly what the error contract exists to prevent.

`tui/catalog.py` therefore carries `UNIMPLEMENTED = frozenset({"ocr"})` and
nothing else about any tool.

## Alternatives considered

**Offer every registered tool and let the router explain.** Does not work.
`ocr`'s `LocalStrategy.is_available()` reports on its *dependencies*, not on
whether `run()` is implemented, so on a machine with Tesseract installed the
router would route to it and only then fail.

**Add `implemented: bool = True` to `ToolSpec`.** The right long-term answer,
and a change to Core. `phases.md` says of the TUI and the MCP server:

> If either requires a change below the interface layer, that is a signal the
> core contracts are wrong — treat it as a finding, not a workaround.

Treated as a finding. It is the **fourth** instance of the same gap
`current-status.md` already records — `ToolSpec` cannot say "I produce no
output", "my extension depends on a parameter", or "carry configuration to a
strategy" — and that document says all of them *"should be decided together"*.
Deciding four core contracts is a milestone's worth of work and would make M7 a
Core change. Deferred deliberately, with the pressure written down.

**Filter by hand to the tools the CLI happens to expose.** What this is, minus
the enforcement. Rejected in that form: an unchecked list is the drift the
registry exists to prevent.

**A bespoke screen per tool.** v2's failure, re-enacted. Rejected outright.

## Consequences

- Eighteen tools cost one screen class. `crop`, added in this same milestone,
  needed no TUI code at all.
- The TUI cannot offer a control the registry cannot describe. A tool wanting a
  file browser, a colour picker or a multi-select must first say so in `Param`,
  where every other interface can see it — which is the constraint working.
- `catalog.py` carries one line of debt until M8. Deleting it is the whole
  change, and a test fails if someone forgets.
- Any `Param.type_` outside the closed set renders as a text field rather than
  raising. A tool declaring a sixth type is a registry bug, and hiding eighteen
  working tools behind one bad declaration would be the wrong response.

## Enforcement

- `tests/unit/test_tui.py::test_the_tui_names_no_tool_except_the_unimplemented_one`
  — an AST scan of every string constant in `docmax/tui/`, failing if any equals
  a registered tool name.
- `test_the_tui_offers_exactly_what_the_cli_exposes` — the offered set must be a
  subset of the CLI's commands, so `catalog.py` cannot drift from the CLI in
  either direction.
- `test_ocr_is_registered_and_not_offered` — pins the exception, and fails the
  day `ocr` ships unless `UNIMPLEMENTED` is emptied.
- `test_every_offered_tool_opens_a_form` — drives a `Pilot` through all eighteen
  and asserts a widget exists for every declared `Param`.
