# ADR 0036 — `ToolSpec` says when a tool produces no output, so an interface can stop asking for one

**Status:** Accepted · 2026-08-30

## Context

`get-info` and `permissions` are read-only: they answer a question about a
document and write nothing. `EngineStrategy.run` still requires an
`OutputTarget` argument by contract, so both tools' `local.py` accept one and
ignore it — a seam their own docstrings already named:

> `ToolSpec` has no way to say "this tool produces nothing", so the router
> resolves a destination that is never used, and an interface has to avoid
> handing it a path that would trip `OutputExistsError` for a file it was
> never going to write.
> — `tools/get_info/local.py`

The CLI worked around this per-command: `cli/commands.py` never declares a
`-o` option for `get-info` or `permissions` in the first place, and
`cli/execution.py::execute_read_only` builds an `OutputTarget` by hand —
`OutputTarget(destination=document.path, force=True)` — rather than calling
`router.target_for`, specifically to avoid checking a destination that will
never be written.

The TUI has no such workaround, because it has no per-tool code to put one
in — `tui/app.py` and `tui/forms.py` build one form shape from the registry
for every tool. It therefore always renders the "output (required)" field,
always requires it to be filled in, and always calls
`router.target_for(..., requested=<whatever the user typed>, ...)`. For a
report-only tool the result is a real, reported bug: the field demands a
path, `OutputTarget.resolve` resolves and checks a destination for it, and
the run then succeeds while writing nothing there — `get-info` and
`permissions` finish with **"Wrote nothing · local engine"**, having
correctly reported what was asked, with no way for the TUI to say so.

`docs/planning/current-status.md` already named this exact fix and deferred
it, grouped with two unrelated seams — "output extension depends on a
parameter" and "config reaches a strategy" — under "decide all three
together." That grouping no longer holds for this one: the other two are
about a tool's **output value** or its **own configuration**; this one is
about whether the tool has an output at all, which is the more fundamental
question a router, a CLI, a TUI, and an MCP schema all have to answer before
either of the other two questions is even reachable. ADR 0031 made the same
call for `produces_directory` against the same three-seams list, for the
same reason: not speculative, not entangled with the other two, and the
direct cause of a confirmed, reported defect.

## Decision

**`ToolSpec` gains one field: `produces_output: bool = True`.**

```python
@dataclass(frozen=True, slots=True)
class ToolSpec:
    ...
    produces_output: bool = True
```

Set `False` on exactly `get-info` and `permissions` — the two tools whose
`run()` already documents `outputs=()` unconditionally, in every branch, for
every input. `metadata` is deliberately **not** included: its `run()` writes
a new document when `set` or `clear` is given and writes nothing otherwise,
so whether it "produces output" depends on parameters supplied at call time,
not on the tool's identity. A static `bool` on `ToolSpec` cannot express
"sometimes," and forcing it to would be wrong for exactly the runs where the
user *is* setting metadata. `metadata` keeps the default (`True`) and keeps
asking for `-o` in the TUI exactly as it does today — not a regression, since
that is what it has always done, and a real fix needs the output requirement
to react to the `set`/`clear` fields' own values, which is a different,
larger change than a per-tool static flag. It is recorded here as the
follow-up this ADR deliberately does not make.

**`EngineRouter.target_for` reads the flag and skips `OutputTarget.resolve`
entirely when it is `False`**, building the same throwaway target
`execute_read_only` already builds by hand:

```python
def target_for(self, tool_name, docs, *, requested=None, force=False):
    spec = self.lookup(tool_name)
    if not spec.produces_output:
        if not docs:
            raise InvalidParameterError(...)
        return OutputTarget(destination=docs[0].path, force=True)
    return OutputTarget.resolve(inputs=docs, requested=requested, ...)
```

This is additive, not a replacement: `cli/execution.py::execute_read_only`
keeps building its own target by hand rather than being rewired to call
`target_for` — it already works, it is not the thing this ADR is fixing, and
"do not touch what already works" beats "have only one implementation" when
the second implementation is three lines old and predates the flag it could
now use. What changes is that `target_for` — the entry point every *other*
interface calls, including the TUI and any future one — now has the same
correct behaviour `execute_read_only` carved out for itself, for free,
driven by the registry instead of by a hand-written list of tool names.

**Every interface that builds a form or a CLI option reads `produces_output`
to decide whether to ask for one at all**, rather than asking and then
discovering the answer did not matter. The TUI's `RunScreen` renders the
output field, its label, and its Browse button only when
`spec.produces_output` is `True`, and skips the "output is required" check
in `_request` the same way. This is the one part of the fix with no CLI
analogue to mirror, because the CLI's per-command `typer` functions already
made this decision per tool by simply not declaring `-o` — the TUI had no
equivalent per-tool decision point until now, which is the entire reason this
bug existed only there.

**No tool name is compared anywhere.** `get-info/tool.py` and
`permissions/tool.py` each set `produces_output=False` on their own
`ToolSpec` — the same way `split/tool.py` and `to_images/tool.py` set
`produces_directory=True` on theirs. Nothing in `core`, the TUI, or the CLI
contains the string `"get-info"` or `"permissions"` to make this decision;
a future report-only tool needs the same one-line declaration and nothing
else.

## Alternatives considered

**Have the TUI special-case the known report-only tools by name.** The
alternative every prior seam in this project has rejected, for the reason
`tui/catalog.py`'s own docstring gives for its one remaining named exception:
a hand-written list is a list that drifts the moment a twentieth tool needs
the same treatment, silently, until someone notices the field is wrong again.

**Have `EngineStrategy.run` accept `OutputTarget | None`.** Rejected: it
would put `if target is not None` at the top of every one of the other
seventeen strategies that never need it, for the benefit of two tools —
exactly the reasoning `protocols.py` already gives for why `progress` and
`cancellation` are required rather than optional.

**Fold `metadata` in too, treating any tool with an optional `-o` as
`produces_output=False`.** Rejected: `metadata` genuinely does produce
output on some calls, and a `bool` that lies half the time is worse than a
form that asks a question it does not always need answered. See Decision.

## Consequences

- `get-info` and `permissions` no longer show "output (required)" in the
  TUI, no longer require a path the user has no reason to supply, and no
  longer report "Wrote nothing" for a run that correctly wrote nothing.
- `EngineRouter.target_for` gained one branch, read by every current and
  future caller — the CLI's own `execute_read_only` is simply no longer the
  only place this logic exists.
- **Two seams remain** of the three `current-status.md` grouped: "output
  extension depends on a parameter," and "config reaches a strategy." Both
  are exactly as open as before; neither is entangled with this one.
- `metadata`'s conditional output requirement in the TUI remains unfixed,
  by deliberate scope decision, and is named here for whoever picks it up.

## Enforcement

- `tests/unit/test_router.py` — `target_for` returns a target pointing at
  the first document, unresolved and unchecked, for a fake spec declaring
  `produces_output=False`, and continues to resolve normally for one that
  does not set the field at all (the default).
- `tests/unit/test_tui.py` — the output field, its label, and its Browse
  button are absent from `get-info`'s and `permissions`' generated forms;
  `_request` builds a request with no output for such a tool; a full Pilot
  run of `get-info` succeeds with no output field ever touched.
