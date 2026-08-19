# Plan 01 — One declaration, every surface

**Goal:** adding a tool means writing `tool.py` + `local.py` and nothing else.
No CLI command, no API schema, no TUI form, no MCP definition, no entry in a
central table — anywhere.

**Do this before tool #2.** The window is open exactly now: `cli/main.py` has
one hand-written command (`doctor`) and it is not a tool. The moment a
hand-written `merge` command exists, this becomes a refactor of N commands
instead of a design decision.

---

## 1. Where things actually stand

| Thing | State |
|---|---|
| `ToolSpec` / `Param` in `core/registry.py` | written, complete enough |
| `Param` docstring promising CLI/TUI/API/MCP rendering | written, **unimplemented** |
| `core/router.py` | **does not exist** — yet docstrings across the tree refer to "the router" |
| Per-tool CLI commands | **none yet** |
| `EXTERNAL_BINARIES` in `cli/main.py` | a hardcoded central tool→binary map — see R1 |

The router is the missing keystone. It should be written generically the first
time, because it will never be rewritten generically later.

## 2. Rectifications to existing code

### R1 — `EXTERNAL_BINARIES` violates ADR 0002

`cli/main.py:31` holds:

```python
EXTERNAL_BINARIES: dict[str, tuple[str, ...]] = {
    "tesseract": ("ocr",),
    "gs": ("compress", "pdfa"),
    ...
}
```

This is precisely the central file ADR 0002 exists to abolish: adding tool #51
requires editing it, nothing detects the drift when someone forgets, and it
lives in `cli`, so the server and the MCP adapter cannot see it.

**Fix:** the dependency belongs to the tool that has it.

```python
# core/registry.py — ToolSpec gains:
    #: External programs this tool's local engine needs. `doctor` derives its
    #: whole table from these; `setup` derives what it installs. Declared here
    #: rather than in a central map so tool #51 touches no shared file.
    requires_binaries: tuple[str, ...] = ()
```

`doctor` then inverts the registry at call time:

```python
def binaries_needed() -> dict[str, tuple[str, ...]]:
    """binary -> tools that need it, derived from the registry."""
    index: dict[str, list[str]] = {}
    for spec in all_specs():
        for binary in spec.requires_binaries:
            index.setdefault(binary, []).append(spec.name)
    return {b: tuple(sorted(t)) for b, t in sorted(index.items())}
```

Delete `EXTERNAL_BINARIES`. `ocr/local.py:BINARIES` becomes the spec's
`requires_binaries` and stops being duplicated.

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
    #: See R1.
    requires_binaries: tuple[str, ...] = ()
```

`produces_directory` is the one that will hurt if it is late: `split` is M2,
and without it `OutputTarget.resolve` derives a `.pdf` destination for a tool
that produces a folder.

### R4 — `merge/tool.py` updated to match

Mechanical. It is also the file every future `tool.py` gets copied from, so it
should show the new fields even where they hold their defaults.

## 3. New modules

### `core/router.py` — the missing keystone

Two responsibilities and nothing else. Must not import `rich`, `typer`, or any
tool module.

```python
def resolve_engine(
    spec: ToolSpec,
    requested: Engine = Engine.AUTO,
    *,
    offline: bool = False,
    consent: ConsentPolicy | None = None,
) -> tuple[Engine, EngineStrategy]:
    """Pick the engine that will run, or explain why none can.

    AUTO prefers LOCAL when available (private and free), and falls back to
    CLOUD only when the tool supports it, the user is not offline, and consent
    for this tool has been given. An explicit request is never silently
    overridden — asking for LOCAL and getting CLOUD would upload a document the
    user asked to keep local.
    """
```

Failure modes, all already typed in `errors.py`: `EngineNotSupportedError`,
`NoEngineAvailableError` (quoting **both** strategies' `unavailable_reason()`),
`ConsentRequiredError`.

```python
def run_tool(
    spec: ToolSpec,
    inputs: Sequence[str | Path],
    *,
    output: Path | None = None,
    force: bool = False,
    engine: Engine = Engine.AUTO,
    params: Mapping[str, Any] | None = None,
    progress: ProgressSink | None = None,
    cancellation: CancellationToken | None = None,
) -> ToolResult:
    """The one code path every interface calls: CLI, server, TUI, MCP, batch.

    refs -> target -> params -> engine -> run -> result. Anything escaping a
    strategy that is not a DocMaxError is wrapped in InternalError here, which
    is what makes "no tracebacks" true rather than aspirational.
    """
```

If four interfaces each assemble those six steps themselves, they will disagree
about at least one of them, and the disagreement will be a bug in whichever one
nobody uses daily.

### `core/params.py` — validate once

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

### `cli/build.py` — generate the commands

```python
def register_tool_commands(app: typer.Typer) -> None:
    """Attach one generated command per registered ToolSpec."""
```

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

### `cli/errors.py` — the one exit boundary

`main.py` claims to be "where typed errors become exit codes" but contains no
such mapping. One decorator wrapping every generated command:

```python
EXIT_CODES = {
    "input": 2, "output": 3, "dependency": 4,
    "engine": 5, "cloud": 6, "cancelled": 130, "internal": 70,
}
```

Match on the family prefix (`code.value.split(".")[0]`), so a new `ErrorCode`
member never requires a new entry here. Shared with
[plan 04](04-json-envelope.md), which uses the same boundary.

## 4. The standing rule

> **A tool is declared once, in its `ToolSpec`. Every interface is generated
> from the declaration. No interface may contain per-tool code.**

Recorded in [ADR 0006](../adr/0006-spec-driven-surfaces.md). Enforced by
`tests/hygiene/test_no_handwritten_commands.py`, which AST-walks `docmax/cli`
(and `docmax/server`, and `docmax/mcp` when it lands) and fails when a decorated
command name, a route path segment, or a dict key matches a registered tool
name.

The test is the point. ADR 0002 already forbade central tool tables, and one
appeared anyway in `cli/main.py:31` — because nothing was checking. A rule
without a test is a comment.

Permitted exceptions, allowlisted explicitly in the test: `doctor`, `setup`,
`tools`, `formats`, `config`, `version`, `mcp`, `serve` — commands that are
*about* tools rather than being tools.

## 5. Acceptance

- [ ] `EXTERNAL_BINARIES` deleted; `docmax doctor` derives its table from the registry
- [ ] `merge` has a working CLI command that nobody wrote
- [ ] registering a `ToolSpec` with a fake name makes a command appear with no other edit
- [ ] `lint-imports` still passes; `core` imports no `typer`
- [ ] `docmax --help` imports neither `pypdf` nor `cv2` (asserted in `test_no_heavy_imports.py`)
- [ ] `test_no_handwritten_commands.py` fails when a `@app.command()` named `merge` is added
- [ ] `docmax merge --nonexistent-flag` and the equivalent API call produce the same `ErrorCode`
