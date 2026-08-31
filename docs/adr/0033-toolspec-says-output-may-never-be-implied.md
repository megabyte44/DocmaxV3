# ADR 0033 — `ToolSpec` says when output may never be implied, so the TUI stops suggesting one that is wrong

**Status:** Accepted · 2026-08-30

## Context

[Issue #24](https://github.com/megabyte44/docmax/issues/24): the TUI's `convert`
run screen let a user leave the output field looking optional and hinted at a
destination that does not exist. `RunScreen.compose` built the output field's
placeholder from `spec.default_suffix` unconditionally —
`f"path to write the {spec.default_suffix} result to"` — and `default_suffix`
is `".pdf"` for every tool, `convert` included, because it is the dataclass
default and nothing had ever overridden it.

For most tools that placeholder is accurate: DocMax is mostly PDF-to-PDF, and
`.pdf` genuinely is what running the tool produces. For `convert` it is not
merely unhelpful, it is wrong on every single run — Pandoc has no PDF writer,
[ADR 0011](0011-convert-is-pandoc-only.md) refuses `--to pdf` outright, and the
real extension is determined by `--to`, a parameter, not a constant. The TUI
was telling the truth about eighteen tools and actively lying about the
nineteenth, and had no way to know which was which: `cli/commands.py` already
gets this right — `convert`'s `output` is a plain, required `Path` with no
default, and `metadata`'s is a hand-written check against `--set`/`--clear` —
but neither fact reaches `ToolSpec`, so `tui/app.py`'s generated form cannot
read it. That is the shape CLAUDE.md's rule 1 names directly: a fact about one
tool, encoded per-interface instead of once on the spec.

**This is not the "output extension depends on a parameter" seam**
`docs/planning/current-status.md` already lists as open and explicitly says
should be decided together with two others (`ToolSpec` cannot say "produces no
output"; cannot carry configuration to a strategy). That seam is about a
*positive* claim this ADR does not make: what `convert`'s correct output
extension actually is for a given `--to`. This decision makes no such claim —
`default_suffix` still exists, still says `.pdf`, and still is not to be
trusted for `convert`. What is decided here is narrower and purely negative:
that no destination — not `default_suffix`, not any other guess — should ever
be *implied* for a tool where guessing would be actively wrong rather than
merely absent. [ADR 0031](0031-toolspec-says-when-output-is-a-directory.md)
drew the identical line for `produces_directory`, for the same reason: it does
not say what the correct directory name is either, only that the file-shaped
guessing logic must not run.

## Decision

**`ToolSpec` gains one field: `output_required: bool = False`.**

```python
@dataclass(frozen=True, slots=True)
class ToolSpec:
    ...
    output_required: bool = False
```

Set `True` on exactly two tools: `convert`, because its real extension is a
parameter and `default_suffix` is wrong for it in every case rather than some;
and `metadata`, because a write with no explicit `-o` would derive a
destination sharing the input's own extension and collide with it — the exact
`InPlaceOverwriteError` shape [ADR 0028](0028-the-mcp-tool-surface-is-the-registry.md)'s
neighbours already refuse for `merge`, except `metadata` promises in its own
docstring never to edit a file in place, so implying that destination is worse
than merely refusing it. Every other tool keeps the default `False`, unchanged.

**`metadata` keeps its own runtime check in `cli/commands.py`.** *Whether*
`-o` is required for a given `metadata` invocation depends on `--set` and
`--clear`, which are ordinary parameters `ToolSpec` has no way to see —
nothing here changes that, and nothing could without `Param` growing
inter-parameter dependencies it does not have and should not. What changes is
that the check now reads `get_tool("metadata").output_required` instead of
assuming the fact inline, so the CLI's behaviour and the spec's own claim
about the tool cannot silently drift apart the way they already had for
`convert`. `convert`'s own required `Path` needed no equivalent change: a
required option with no default already says, structurally, exactly what
`output_required=True` says declaratively — there was no hand-rolled branch to
remove, only the fact's absence from `ToolSpec` to fix.

**`tui/app.py:RunScreen.compose` reads it to change one thing: the output
field's placeholder.** Every tool's output stays mandatory in the TUI
regardless of this field — that has been true unconditionally since the
`merge`-derivation fix landed
(`tests/unit/test_tui.py::test_a_form_with_no_output_is_a_typed_error_not_a_crash`),
for the reason its own docstring gives: most of DocMax is PDF-to-PDF, so an
omitted output derives to the first input and is refused as
`InPlaceOverwriteError` regardless of which tool it was. This ADR does not
touch that; weakening it for any tool was never on the table. What
`output_required` changes is `_output_placeholder`: when true, the hint drops
`default_suffix` entirely — `"path to write the result to"` — rather than
naming an extension the tool can never produce.

**`OutputTarget.resolve` is untouched.** Both `convert` and `metadata` already
require an explicit `-o` in the CLI and the TUI before a request is even
built, so the derive-from-first-input and extensionless-suffix-filling
branches never run for them in practice today. Threading `output_required`
through the router as ADR 0031 threaded `produces_directory` would guard
against a path neither interface currently takes, for a cost — another
parameter on `OutputTarget.resolve`, another thing every caller must pass
correctly — with no live bug behind it. If a future caller (a pipeline stage,
`batch`, `watch`, the HTTP server's own destination fallback) starts deriving
a destination for `convert` or `metadata` without asking first, that is new
evidence, and this ADR does not pre-empt writing a fresh one when it appears.

## Alternatives considered

**Fold this into the "output extension depends on a parameter" seam and wait
for all three to be decided together.** The github issue this ADR answers is
a live, confirmed usability bug — a wrong claim shown to every user who opens
`convert` in the TUI — not a speculative gap. ADR 0031 made exactly this call
for `produces_directory` and gave the reasoning this ADR repeats: deferring a
fix for a live defect to await a larger, unrelated decision serves nobody, and
this decision does not touch the larger seam's actual substance (what the
correct extension is). The three seams remain exactly as open as
`current-status.md` already records; this closes no part of them.

**Have `tui/app.py` special-case `convert` and `metadata` by name.** Rejected
outright — this is the precise shape `test_the_tui_names_no_tool_except_the_unimplemented_one`
exists to forbid, and CLAUDE.md rule 1's own history: `EXTERNAL_BINARIES` and
`cli/commands.py`'s nine hand-written commands were both "reasonable in the
moment" per-tool exceptions that nothing was checking.

**Rename `default_suffix` to something like `Optional[str]`, `None` meaning
"do not guess."** Considered, since it would need no second field. Rejected
because it conflates two different facts under one name: `default_suffix` is
consumed today by `OutputTarget.resolve`'s two guessing branches, by
`tui/app.py`'s preview pane (`_show_preview`'s `"a {suffix} file"` line, which
is accurate for `convert` in the sense that its *fallback* value is `.pdf`
even though that fallback is never reached), and by the runners' own
destination derivation. Making it `None` for `convert` would have to touch
every one of those call sites to handle the new case, several of which
(`_show_preview`, the runners) are not part of this bug and have no test
asserting new behaviour for them. A second, narrowly-scoped boolean is the
smaller, more legible change — the same trade-off ADR 0031 made explicitly in
choosing a second field over overloading `default_suffix` there too.

## Consequences

- **The placeholder tells the truth for every tool**, `convert` included: the
  TUI form for `convert` now reads `"path to write the result to"`, naming no
  extension it cannot produce.
- **`cli/commands.py`'s `metadata` check and `ToolSpec`'s own claim about the
  tool cannot drift apart** the way `convert`'s already had — the runtime
  check reads `get_tool("metadata").output_required` rather than assuming it.
- **One more field for a tool author to set**, defaulting to `False` — the
  common case — so seventeen of nineteen registered tools needed no edit.
- **The larger "output extension depends on a parameter" seam is unchanged**
  and remains exactly as open as `current-status.md` records it. This ADR
  answers "should a destination ever be implied here," not "what is the
  correct one" — the second question is still owed its own decision, together
  with the other two `current-status.md` names.

## Enforcement

- `tests/unit/test_registry.py` — every registered tool's `output_required`
  is asserted against an explicit expected set (`{"convert", "metadata"}`),
  so a nineteenth tool with a wrong default is caught rather than assumed.
- `tests/unit/test_tui.py` — `convert`'s generated output field placeholder
  names no extension; an ordinary tool's (`crop`) still does. Output stays
  required in the TUI for every tool regardless, held by the existing
  `test_a_form_with_no_output_is_a_typed_error_not_a_crash` and
  `test_merge_with_no_output_is_a_typed_error_not_a_silent_collision`, which
  this ADR does not touch.
- `tests/unit/test_cli.py` — `metadata --set` with no `-o` is still refused,
  and the refusal still traces back to `get_tool("metadata").output_required`
  rather than an inline assumption a future edit could silently drop.
