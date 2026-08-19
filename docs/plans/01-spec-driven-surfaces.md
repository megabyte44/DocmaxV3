# Plan 01 — One declaration, every surface

**Goal:** adding a tool means writing `tool.py` + `local.py` and nothing else.
No CLI command, no API schema, no TUI form, no MCP definition, no entry in a
central table — anywhere.

**Do this at ten tools, not at forty.** This is now a refactor rather than a
greenfield decision — the M1–M3 stack landed nine hand-written commands. Ten is
still cheap. Forty is a rewrite nobody schedules.

---

## 1. Where things actually stand

Revised after the M1–M3 stack (PR #10) landed `core/router.py`, the CLI wiring,
and ten tools.

| Thing | State |
|---|---|
| `ToolSpec` / `Param` in `core/registry.py` | written; **unchanged by the stack** — R2 and R3 below still apply in full |
| `Param` docstring promising CLI/TUI/API/MCP rendering | written, **still unimplemented** |
| `core/router.py` | ✅ exists, with the precedence ladder in one place |
| `cli/execution.py` | ✅ exists — one `execute()` that every command calls |
| Per-tool CLI commands | ❌ **nine hand-written** in `cli/commands.py` |
| `EXTERNAL_BINARIES` | moved to `tools/_binaries.py` — layering fixed, table still central. See R1 |

Two of the three things this plan originally called for already exist. The
router is written and `cli/execution.py` is exactly the single funnel rule 2
asks for — commands name their arguments, hand off to `execute()`, and render.

What remains is the last step: the commands themselves are still typed out by
hand, nine times, and the spec fields that would let them be generated were
never added.

The stack's own code says so. `cli/commands.py` opens with *"Each command here
does the same three things and nothing else"* — nine near-identical functions,
which is the definition of something that should be generated. And
`tools/get_info/local.py` notes that the clean fix for read-only tools is *"a
`produces_output` flag on `ToolSpec`, which is a core change and is being
reported rather than made."* That is R3, identified independently.

## 2. Rectifications to existing code

### R1 — the binary table: half fixed, and the other half is arguable

**Superseded in part.** This plan originally said `EXTERNAL_BINARIES` in
`cli/main.py` was a central table in the wrong layer. The stack moved it to
`tools/_binaries.py` and said why, correctly: `tools` sits below `cli`, so the
engines can now consult the same list `doctor` reports from, instead of two
lists that can disagree. It also gained per-platform `install` commands and an
`install_hint()` — most of [plan 06](06-doctor-remedies.md), built already.

What remains is narrower, and the original plan was too blunt about it. The
`Binary.used_by` field still names tools from a central list:

```python
Binary(name="gs", used_by=("compress", "pdfa"), ...)
```

and `pdfa`, `to-images`, and `convert` do not exist yet. The stack's docstring
defends this deliberately: *"a list that grows tool by tool is a list that gets
out of step with the roadmap."* That is a fair point, and it kills the naive
version of this rectification — moving `used_by` wholesale onto `ToolSpec`
would make `doctor` unable to report on binaries for unwritten tools.

**Revised fix — keep the catalogue, invert the ownership, check for drift:**

1. `EXTERNAL_BINARIES` **stays** in `tools/_binaries.py`. It is knowledge about
   *binaries* — names, platform commands, install lines — which genuinely is
   global. `used_by` stays too, as the roadmap's forward declaration.
2. `ToolSpec` gains `requires_binaries`, so a tool that exists declares its own
   dependency where the engine can reach it without a lookup:

   ```python
   #: External programs this tool's local engine needs, by Binary.name.
   requires_binaries: tuple[str, ...] = ()
   ```

3. A hygiene test asserts the two agree for every **registered** tool:

   ```python
   def test_binary_catalogue_matches_the_registry() -> None:
       """used_by may name unwritten tools. It may not contradict written ones."""
   ```

That keeps `doctor` roadmap-aware, gives engines a local declaration, and makes
the two impossible to drift apart. `ocr/local.py:BINARIES` then stops being a
third copy.

### R2 — `Param` is missing what 45 tools will need

Adding these now costs nothing; adding them once 20 tools are declared means
touching 20 files.

```python
@dataclass(frozen=True, slots=True)
class Param:
    name: str
    description: str
    type_: str = "str"          # str | int | float | bool | path
    default: Any = None
    required: bool = False
    choices: tuple[str, ...] = ()

    # -- new ---------------------------------------------------------------
    #: Short flag without the dash, e.g. "p" -> -p. Optional; a collision is a
    #: registration-time InternalError, not a runtime surprise.
    short: str | None = None
    #: Repeatable. --page 1 --page 4 arrives as a tuple. Needed by pages,
    #: split, rotate, reorder, to-images — i.e. most of M2.
    multiple: bool = False
    #: Hidden from the default --help, shown by --help-all. Keeps the common
    #: case readable once a tool has fourteen knobs.
    advanced: bool = False
```

Keep `type_` a closed set of strings. It is the reason every surface can render
a param without importing anything; widening it to arbitrary types would
quietly re-couple the registry to the CLI.

### R3 — `ToolSpec` is missing three facts the surfaces need

```python
    #: Extensions the tool accepts, e.g. (".pdf",). Empty means "anything".
    #: Lets the router reject a bad input before an engine is imported, and
    #: makes `formats` (already cited in UnsupportedFormatError.default_remedy)
    #: something that can actually be built.
    input_suffixes: tuple[str, ...] = (".pdf",)
    #: True for split / to-images: output is a directory, so the router picks
    #: atomic_dir rather than atomic_write.
    produces_directory: bool = False
    #: False for get-info and a bare `metadata` read: the tool writes nothing,
    #: so no destination should be resolved or checked.
    produces_output: bool = True
    #: See R1.
    requires_binaries: tuple[str, ...] = ()
```

`produces_output` is not speculative — it is already costing something.
`cli/commands.py` documents the workaround it forced:

> *"`get-info` and a bare `metadata` write nothing, but `EngineStrategy.run`
> requires an `OutputTarget`. Those two build one directly rather than through
> `router.target_for`, because resolution would check a destination that is
> never written — and would refuse the run with `OutputExistsError` if a file
> happened to sit at the derived path."*

The workaround works — nothing is broken for users today. The cost is that two
commands construct an `OutputTarget` by hand rather than going through the
router, which is the one thing rule 2 exists to prevent, and every future
read-only tool (`get-info`, `metadata`, `formats`, anything in M9's inspection
work) will copy the same bypass. A one-line spec field retires it.

`produces_directory` is the same shape — `split` writes a directory, and
`OutputTarget.resolve`'s `default_suffix` logic assumes a file.

### R4 — `merge/tool.py` updated to match

Mechanical. It is also the file every future `tool.py` gets copied from, so it
should show the new fields even where they hold their defaults.

## 3. Modules

### `core/router.py` — ✅ already built, one thing to fold in

`EngineRouter` exists with `resolve()`, `target_for()`, and `run()`, and the
precedence ladder lives in exactly one place. Nothing in this plan changes it.

One consequence to collect once R3 lands: `cli/execution.py` currently exposes
both `execute()` and `execute_read_only()`, and the second exists *only*
because a tool cannot say it writes nothing. With `produces_output` on
`ToolSpec`, `target_for()` returns `None` for those tools and the two funnels
become one.

### `core/params.py` — validate once (new)

```python
def validate(spec: ToolSpec, raw: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce and check argv/JSON/form input against the declared params.

    Raises InvalidParameterError naming the parameter, the offending value, and
    the accepted set. The CLI, the API server, and the MCP adapter all call
    this, so a bad --mode produces the same error text in all three.
    """
```

Unknown parameter names are an error, not a silent ignore — `**params: Any` in
`EngineStrategy.run` means a typo would otherwise vanish without a trace.

### `cli/build.py` — generate the commands (new; replaces `cli/commands.py`)

```python
def register_tool_commands(app: typer.Typer) -> None:
    """Attach one generated command per registered ToolSpec."""
```

`cli/commands.py` is deleted by this change. Its nine functions become nine
`ToolSpec.params` declarations, most of which already exist — the option
definitions in `commands.py` (`_EngineOption`, `_ForceOption`, `_PagesOption`,
`_DryRunOption`) are exactly the uniform set below, already factored out by
hand because they had to mean the same thing seven times.

Do this migration one command at a time, keeping `test_cli_m2.py`,
`test_cli_merge.py`, and `test_cli_compress.py` green throughout. Those 867
tests are the safety net that makes this refactor cheap; they were not there
when this plan was first written.

Each generated command gets, uniformly:

- variadic input paths when `accepts_multiple_inputs`, otherwise exactly one
- `-o/--output`, `--force`, `--engine {auto,local,cloud}`, `--json`, `--quiet`
- one option per `Param`, typed from `type_`; `multiple` becomes a list option,
  `choices` becomes an enum, `advanced` becomes `hidden=True`
- help text assembled from `summary` and each `Param.description`

Build the signature with `inspect.Signature` and assign `__signature__` on the
callback — typer reads that. Do **not** build a source string and `exec` it.

Cost check: this imports every `tool.py` at CLI startup. That is exactly what
`ToolSpec` was designed for — metadata only, no heavy imports — and
`test_no_heavy_imports.py` already guards it. Extend that test to assert that
building the full command tree imports neither `pypdf` nor `cv2`.

### Exit codes — extend `cli/execution.py`, do not add a module

The boundary exists. `cli/execution.py` defines `EXIT_CANCELLED = 130` and
`EXIT_FAILURE = 1`, and every command already funnels through it — so this is
now a five-line change rather than a new file.

What is missing is that **every non-cancellation failure is exit 1**. A script
cannot tell "you passed a bad page range" from "Ghostscript is not installed"
from "this is our bug", which is the whole point of having a stable
`ErrorCode` taxonomy.

```python
EXIT_BY_FAMILY = {
    "input": 2, "output": 3, "dependency": 4,
    "engine": 5, "cloud": 6, "internal": 70,
}
```

Match on the family prefix (`code.value.split(".")[0]`) so a new `ErrorCode`
member never requires a new entry, and keep `EXIT_FAILURE = 1` as the fallback
for an unmatched family. Shared with [plan 04](04-json-envelope.md).

## 4. The standing rule

> **A tool is declared once, in its `ToolSpec`. Every interface is generated
> from the declaration. No interface may contain per-tool code.**

Recorded in [ADR 0010](../adr/0010-spec-driven-surfaces.md). Enforced by
`tests/hygiene/test_no_handwritten_commands.py`, which AST-walks `docmax/cli`
(and `docmax/server`, and `docmax/mcp` when it lands) and fails when a decorated
command name, a route path segment, or a dict key matches a registered tool
name.

The test is the point, and the repository has now demonstrated it twice. ADR
0002 forbade central tool tables; one appeared in `cli/main.py` anyway. ADR 0002
also implied tools should not need per-tool interface code; nine such commands
were then written. Both were reasonable at the time, and both happened because
nothing was checking. **A rule without a test is a comment.**

Permitted exceptions, allowlisted explicitly in the test: `doctor`, `setup`,
`tools`, `formats`, `config`, `version`, `mcp`, `serve` — commands that are
*about* tools rather than being tools.

## 5. Acceptance

- [ ] `cli/commands.py` is deleted; all ten tools have generated commands
- [ ] `test_cli_m2.py`, `test_cli_merge.py`, `test_cli_compress.py` pass unchanged
- [ ] registering a `ToolSpec` with a fake name makes a command appear with no other edit
- [ ] `ToolSpec` carries `produces_output`; `execute_read_only()` is gone
- [ ] `Binary.used_by` and `ToolSpec.requires_binaries` agree for every registered tool
- [ ] failures exit with a family-specific code, not a blanket 1
- [ ] `lint-imports` still passes; `core` imports no `typer`
- [ ] `docmax --help` imports neither `pypdf` nor `cv2` (asserted in `test_no_heavy_imports.py`)
- [ ] `test_no_handwritten_commands.py` fails when a `@app.command()` named `merge` is added
- [ ] `docmax merge --nonexistent-flag` and the equivalent API call produce the same `ErrorCode`
