# ADR 0032 — `Param` gains `component_labels`, so a composite value gets one labelled field per part

**Status:** Accepted · 2026-08-30

## Context

Manual testing found a real usability gap in `crop`: its one required parameter,
`box`, is a single text field expecting `x,y,width,height` — four numbers with
four different meanings, typed blind into one box with nothing on screen to
say which number is which. The CLI's `--box x,y,width,height` has the same
shape, but a CLI flag's own name and `--help` text carry the explanation; a
bare TUI text input does not.

`Param.type_` is deliberately a small, closed set — `str`, `int`, `float`,
`bool`, `path` — precisely so every consumer (CLI, TUI, the API server, the
M10 MCP server) can render it without guessing. `box` is legitimately a `str`
for all four of those consumers: the CLI still takes one flag, the tool's
own parser (`tools/_box.py`) still takes one string, and nothing about the
wire contract is wrong. What is missing is purely how the TUI *asks* for that
string.

**Only one parameter in the whole registry has this shape.** Checked directly
by listing every `Param` across all nineteen tools: page-range parameters
(`rotate --pages`, `split --pages`, `stamp --pages`, `to-images --pages`,
`watermark --pages`, `pages --select`/`--delete`) are a single free-form
range expression (`1-3,7`) of *unknown, variable* length, not a fixed set of
named parts. `reorder --order` is a permutation whose length is the input's
own page count, unknowable until the document is opened. `metadata --set` is
arbitrary `Key=value` pairs, also unbounded. None of these decompose into a
fixed, small set of independently-labelled fields the way `x,y,width,height`
does — splitting them into separate inputs would need a different UI idiom
entirely (a range picker, a permutation picker, a key-value table), which is
its own piece of work and out of this change's scope. They are named here as
a finding, not fixed.

## Decision

**`Param` gains one field: `component_labels: tuple[str, ...] = ()`.**

```python
@dataclass(frozen=True, slots=True)
class Param:
    ...
    component_labels: tuple[str, ...] = ()
```

Set on exactly one parameter today — `crop`'s `box`, as
`component_labels=("x", "y", "width", "height")` — declared once, in
`crop/tool.py`, alongside the description it already carries. Every other
parameter leaves it empty, the default, and renders exactly as it always has.

**It is presentational only.** The value on the wire is still one `str`,
joined from the labelled parts with commas, parsed by the same
`tools/_box.py::parse` that already validates `--box` — so a malformed value
(a blank part, a non-number, a non-positive width) gets the same typed,
actionable error it always did, from the one place that already owns that
message. Nothing new validates it, and nothing new needs to: `component_labels`
tells the TUI how to *ask*, not how to *check*.

**Only `tui/forms.py` and `tui/app.py` read it.** `field_for` copies it onto
the pre-existing `Field.components`, and `RunScreen.compose` renders one
`Label` + `Input` pair per label instead of a single field when it is
non-empty; `_value_of` joins them back with commas before the value ever
reaches `forms.collect` — which needed no change at all, since it still
receives one string per field name, exactly as before. The CLI's option
parsing, the MCP schema generator, and the API server's request validation
were all checked and read none of `Param`'s other rendering-only fields by
name; they do not read this one either, and behave identically before and
after.

**No heuristic.** Not "does the description contain a comma", not "does the
name look like an acronym" — the tool author states the labels explicitly,
the same way `default_suffix` and `accepts_multiple_inputs` are already
stated rather than inferred.

## Alternatives considered

**A new `Param.type_` member, e.g. `"box"` or `"rect"`.** Rejected: `type_`
drives the CLI's argument type, the MCP JSON Schema's `"type"`, and API
request validation for every consumer, not only the TUI — widening that closed
set for a shape exactly one tool uses today would ripple through three
interfaces that do not need to change, to fix a rendering problem in the
fourth. `component_labels` changes nothing about what kind of value `box` is;
it only says how many independently-meaningful parts a `str` value has.

**A TUI-only lookup table keyed by parameter name** (e.g. `{"box": ("x", "y",
"width", "height")}` living in `tui/forms.py`, no `Param` change at all).
Considered seriously, since it needs no Core change whatsoever. Rejected: it
is a second place describing a fact about `crop`'s own parameter, disconnected
from the registry that is supposed to be the single declaration every
interface reads — the exact anti-pattern `Param`'s own docstring exists to
prevent ("the CLI turns these into options, the TUI into form fields... A tool
that described its parameters only in its `typer` signature could not do any
of that"). It would also silently mislabel any future, unrelated parameter
that happened to also be named `box`.

**Fold this into one of the three deferred `ToolSpec` seams.** Checked against
`current-status.md`'s list (produces no output; output extension depends on a
parameter; carry configuration to a strategy) and it is none of them — this is
about an *input* parameter's shape, not the tool's output or its own runtime
configuration. Not entangled with any of the three, so not deferred alongside
them.

**Split `reorder`'s `order` or a `--pages` range into labelled fields too,
while this was being built.** Out of scope, named in Context: neither has a
fixed arity, so neither fits this mechanism, and building a different one for
them was not asked for and is not free.

## Consequences

- **`crop`'s `box` is the only parameter that renders differently**, and only
  in the TUI. The CLI, the MCP schema, and the API server are byte-for-byte
  unchanged; `tests/unit/test_crop.py`'s existing twenty-five tests hold that.
- **One more optional field on `Param`**, defaulting to empty — every other
  `tool.py` needed no edit, and the AST scan that forbids naming a tool inside
  `docmax.tui` is unaffected: `component_labels` is read generically, with no
  tool name anywhere in `tui/`.
- **A default value that does not have exactly as many comma-separated parts
  as there are labels is not partially guessed at** — `Field.default_component`
  leaves every part blank rather than misassigning a number to the wrong
  label. Not reachable today (`box` has no default), but the rule is total.
- **Filling in only some of the labelled inputs is not blocked by the TUI.**
  The joined value — `10,,500,700` — reaches the router exactly as a
  hand-typed one would, and `tools/_box.py::parse`'s existing error names the
  empty part. Duplicating that check in the TUI would be a second
  implementation of the same message.
- **Three more structured parameters are named as a similar, unfixed gap**:
  every `--pages`-shaped range, `reorder --order`, and `metadata --set`. None
  fits this mechanism's fixed-arity shape; each would need its own UI idiom
  and is recorded here as a finding rather than built.

## Enforcement

- `tests/unit/test_tui.py::test_field_for_copies_component_labels_from_the_param`
  and `::test_a_param_with_no_component_labels_has_an_empty_components_field`
  — the plain-data half, no terminal required.
- `::test_crops_box_param_declares_its_four_components` — pins the one live
  use against the real registry.
- `::test_default_component_splits_a_default_that_fits_the_shape`,
  `::test_default_component_is_blank_when_there_is_no_default`,
  `::test_default_component_is_blank_when_the_default_does_not_fit_the_shape`.
- `::test_box_renders_as_four_labelled_inputs_not_one_blind_field` — no
  `#field-box` widget remains, and the four labels read `x`, `y`, `width`,
  `height`.
- `::test_leaving_every_box_component_empty_is_not_supplied` — the composite
  field's "nothing typed" rule matches every other field's.
- `::test_a_partially_filled_box_still_reaches_the_router_joined` — the
  malformed join reaches the router unchanged, holding that the TUI does not
  duplicate `tools/_box.py`'s validation.
- `::test_every_offered_tool_opens_a_form` — updated to check for whichever
  shape a param's own `component_labels` declares, rather than assuming one
  field per param.
- `tests/unit/test_crop.py` — unmodified and still green, holding that the
  CLI and the tool's own parsing are untouched.
