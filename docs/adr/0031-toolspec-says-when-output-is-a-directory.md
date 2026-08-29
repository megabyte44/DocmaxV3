# ADR 0031 — `ToolSpec` says when output is a directory, so `OutputTarget` can safely fill in a missing extension

**Status:** Accepted · 2026-08-30

## Context

A real usability bug: the TUI let a user leave a file-producing tool's output
extension off entirely — `-o merged` for `merge` — and DocMax wrote a real PDF
to a file literally named `merged`. Windows, with nothing in the name to go on,
opened it in Notepad.

The fix was to have `OutputTarget.resolve` append `default_suffix` to a
`requested` destination that has no suffix of its own, mirroring what the
derive-from-first-input branch already did. That fix broke `split` and
`to-images` the moment it reached the whole suite: both take `-o` as a
**directory** —

> `atomic_dir(target, *, validators=())    # multi-file output (split, to-images)`
> — [ADR 0003](0003-atomic-writes.md)

— and a directory named `parts` has no dot in it *by design*, not by omission.
`split doc.pdf -o parts` started writing its pages into a directory literally
named `parts.pdf`, and every test that globbed `parts/*.pdf` found nothing.

`to_images/tool.py` had already named this exact gap and deferred it:

> Unused in practice: the output is a *directory*, and the CLI requires `-o`.
> `split` has the same shape. See ADR 0011 on why this stays as it is rather
> than `ToolSpec` growing a way to say so.

That deferral was correct when written — nothing depended on the distinction.
The extensionless-output fix now does.

**This is not one of the three seams `current-status.md` says should be
decided together** — "produces no output", "output extension depends on a
parameter", and "carry configuration to a strategy" are all about a tool
wanting something `ToolSpec` cannot currently say about its *output value or
its own configuration*. This is narrower and orthogonal: whether the single
path `-o` already names is a file or a directory. It does not touch, and is
not entangled with, any of the three.

## Decision

**`ToolSpec` gains one field: `produces_directory: bool = False`.**

```python
@dataclass(frozen=True, slots=True)
class ToolSpec:
    ...
    produces_directory: bool = False
```

Set `True` on exactly the two tools ADR 0003 already named as `atomic_dir`
users — `split` and `to-images` — confirmed by re-checking all nineteen
registered tools: grepping every `commands.py` output option for "directory"
in its help text, grepping `tools/*/local.py` for `atomic_dir` and for
`target.destination / ...` (writing *into* the destination rather than *to*
it), and cross-referencing against ADR 0003's own list. All three checks agree
on exactly these two tools and no others.

**`OutputTarget.resolve` takes `produces_directory` as a plain keyword,
exactly as it already takes `default_suffix`** — not a `ToolSpec` object, so
`core/models.py` gains no new dependency on `core/registry.py`:

```python
def resolve(
    cls, *, inputs, requested=None, default_suffix=".pdf",
    produces_directory: bool = False, force=False,
) -> OutputTarget:
```

When `produces_directory` is `True`, the extensionless-destination branch
added for the Notepad bug is skipped entirely — a `requested` directory with
no dot in its name is left exactly as given, exactly as it always was, on
every path (explicit `-o` and the derive-from-first-input branch alike, since
a directory named after the first input's stem should not have `default_suffix`
appended to it either). `EngineRouter.target_for` reads the flag off the spec
it already looked up and forwards it, the same one line that already forwards
`default_suffix`.

**No heuristic.** Not "does the path contain a dot", not "does it already
exist", not "does the name match a known pattern" — all three were explicitly
ruled out because each fails on an ordinary case (a directory *can* have a dot
in its name; a directory that does not exist yet is the common case for a
first run; a naming convention is not an architectural contract). The tool
that is about to write there already knows, unambiguously, whether it opens
one file or creates a directory of many — `ToolSpec` is where every other
per-tool fact about output already lives (`default_suffix`,
`accepts_multiple_inputs`), and this is one more.

## Alternatives considered

**A heuristic in `OutputTarget.resolve` — no dot in the name, or the path
already exists as a directory.** Rejected outright, per the instruction this
ADR was written under: a directory that does not exist yet (the normal case
for `-o parts` on a first run) looks identical to a mistyped file name, and no
string-shape rule distinguishes them. Guessing from existence would also mean
the same `-o value` resolves differently depending on what happened to be on
disk already — the opposite of a destination check anyone can reason about
ahead of time.

**A TUI-only workaround** — skip normalization in `RunScreen._request` for
`split`/`to-images` specifically. Rejected on the same grounds every prior
"resolve it in the interface" alternative in this project has been: the CLI
has the identical bug (`docmax split doc.pdf -o parts` is exactly as broken as
the TUI was), so a TUI-only fix leaves the reference interface wrong and adds
a second, interface-local copy of a rule `OutputTarget` already owns. It would
also require the TUI to know which tools are directory-shaped by some means
other than asking the registry — reintroducing exactly the hand-written
tool list ADR 0021 and `CLAUDE.md` rule 1 forbid.

**Fold this into the three deferred seams and decide all four together.**
Considered, because `current-status.md` is explicit that the three should not
be decided piecemeal. Rejected for this one: it is not the same shape as any
of them (see Context), it is not speculative — it is the direct cause of a
confirmed regression across nine tests the moment the Notepad fix landed — and
deferring a fix for a live regression to await three unrelated, larger
decisions serves nobody. The three seams remain exactly as open as
`current-status.md` already records.

**A `Literal["file", "directory"]` field instead of a `bool`.** No third shape
exists or is anticipated — every tool in the registry writes either one file
or one directory of files — so the smaller type is the honest one. `Param.type_`
being "a small closed set" is exactly this reasoning applied one level up; a
`bool` is as closed a set as two members can be.

## Consequences

- **The Notepad fix now works everywhere it should and nowhere it should not.**
  `merge -o merged` still becomes `merged.pdf`; `split -o parts` still creates
  a plain directory named `parts`.
- **One more field for a tool author to set**, defaulting to `False` — the
  common case — so every existing `tool.py` except these two needed no edit at
  all, and none of the other seventeen changed behaviour.
- **`OutputTarget.resolve` gained one parameter**, threaded through
  `EngineRouter.target_for` the same way `default_suffix` already is. No new
  import, no new coupling between `core/models.py` and `core/registry.py`.
- **The MCP schema is unaffected.** ADR 0028 already describes `output` as "a
  path", not "a file"; `produces_directory` is consumed by `OutputTarget`
  alone and changes nothing a client sees.

## Enforcement

- `tests/unit/test_models.py` — an extensionless explicit destination gets
  `default_suffix` for a file-shaped resolve, is left untouched for a
  directory-shaped one, and both the in-place and already-exists checks still
  see the correctly-shaped path either way.
- `tests/unit/test_router.py` — `target_for` forwards `produces_directory`
  from the looked-up spec, generically, using the suite's own fake tool
  rather than `split` or `to-images` by name.
- `tests/unit/test_cli_m2.py::test_split_writes_a_directory_of_parts` and its
  neighbours — the regression this ADR fixes, held so it cannot recur.
- `tests/unit/test_merge.py::test_an_extensionless_output_still_produces_a_real_pdf`
  — the file-shaped half, end to end through the real registry and router.
