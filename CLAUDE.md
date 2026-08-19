# Working rules for DocMax

Read this before changing anything. Most of the constraints here exist because
v2 shipped the failure they prevent; the ADRs in `docs/adr/` explain each one.

Most rules below are enforced by a test, named under each. Rules 7, 8, and 9
describe work that is planned but not yet built — they are marked, and they say
what to do in the meantime.

If you want to argue with a rule, change the ADR and the test. Do not work
around it.

---

## The rules

### 1. Never write per-tool code in an interface

A tool is declared once, in its `ToolSpec` (`core/registry.py`). The CLI, the
TUI, the HTTP server, and the MCP adapter are **generated** from that
declaration.

- ❌ `@app.command()` named after a tool
- ❌ a route, dict, or `if tool == "ocr"` branch that names a specific tool
- ❌ a central table mapping tools to anything — dependencies, formats, engines
- ✅ a new field on `ToolSpec`, consumed generically by every surface

Facts about a tool live on that tool's spec. If a surface needs to know
something about a tool, the answer is a spec field, not a lookup table.

> ADR 0002, ADR 0010 · `tests/hygiene/test_no_handwritten_commands.py`

**This rule has already been broken twice.** `EXTERNAL_BINARIES` appeared in
`cli/main.py` four days after ADR 0002 forbade that shape (since moved down to
`tools/_binaries.py`, which fixed the layering half of it). Then
`cli/commands.py` grew nine hand-written per-tool commands. Both were
reasonable in the moment; both happened because nothing was checking.

When you add a rule, add the test in the same commit.

### 2. Interfaces route; they do not orchestrate

`core/router.py:EngineRouter` resolves the engine, the target, and the run.
`cli/execution.py:execute` is the CLI's single funnel into it. A command names
its arguments, hands them to `execute`, and renders the result — nothing else.

Do not reassemble those steps in a command or a route handler. A command that
grows an `if offline` is the start of a second implementation of a rule that
must never differ.

> ADR 0010 · `tests/unit/test_cli_merge.py` asserts this structurally

### 3. Library code raises; only `cli` exits

No `sys.exit`, `typer.Exit`, `os._exit`, or `raise SystemExit` in `core`,
`tools`, `cloud_client`, or `server`. Raise a typed error from
`core/errors.py`, with a `remedy` naming the next step.

Anything escaping a tool that is not a `DocMaxError` is a bug.

> `core/errors.py` non-negotiable #1 · `tests/hygiene/test_no_sys_exit.py`

### 4. `core/atomic.py` is the only module that writes

No `open(..., "w")`, `write_text`, `write_bytes`, `cv2.imwrite`, or
`shutil.move` anywhere else. Stage beside the destination, validate, then
`os.replace`.

> ADR 0003 · `tests/hygiene/test_no_direct_writes.py`

### 5. Heavy imports live inside functions

`tool.py` imports only `core`. `pypdf`, `cv2`, `pandas`, `PIL`, and friends are
imported inside the method that uses them — never at module scope. Availability
is checked with `importlib.util.find_spec` or `shutil.which`, never by importing.

`docmax --help` must not import a document library.

> `tests/hygiene/test_no_heavy_imports.py`

### 6. Respect the layering

`core` → `cloud_client` → `tools` → `cli` / `server`. `core` imports no UI
framework and no tool. `cli` and `server` never import each other.

> `.importlinter` · run `lint-imports`

### 7. New tools enter the contract suite

**Not built yet — plan 02.** When it lands: registering a `ToolSpec` requires
an entry in `tests/contract/samples.py`, and a missing entry fails the suite. The
suite then checks ~15 guarantees against it automatically. Do not re-test those
guarantees in a per-tool file; do test behaviour specific to the tool.

When a tool's `run()` becomes real, remove it from `NOT_YET_IMPLEMENTED`.

> ADR 0011 · `tests/contract/`

### 8. Machine-readable output is not optional

**Not built yet — plan 04.** When it lands: every tool supports `--json` for
free through the generated command, progress and logs go to **stderr**, and
stdout carries the envelope and nothing else. `cli/render.py` already routes
progress to stderr for exactly this reason.

Error codes come from `ErrorCode` and are public API — renaming one is a
breaking change. That part is true today.

> `docs/plans/04-json-envelope.md`

### 9. Engines use `ref.materialize()`, not `ref.path`

**Not built yet — plan 03.** `DocumentRef.path` is `Path` today. When the plan
lands it becomes `Path | None`, because a source may be a stream, and engines
that need a real file will open a `materialize()` context manager.

Until then: do not add new code that assumes a source is always on disk more
deeply than it must. Every engine written before this lands is one more to
migrate.

> `docs/plans/03-stream-targets.md`

### 10. Brand literals live in `core/branding.py`

No hardcoded "DocMax" or "docmax" strings elsewhere. `pyproject.toml` and
`branding.py` must agree.

> `tests/hygiene/test_branding.py`

---

## Before you commit

```bash
pytest && ruff check . && ruff format --check . && mypy && lint-imports
```

`pre-commit install` runs most of it for you.

## Adding a tool

1. `src/docmax/tools/<name>/tool.py` — the `ToolSpec`. Copy `merge/tool.py`.
2. `src/docmax/tools/<name>/local.py` — the strategy. Heavy imports inside methods.
3. `src/docmax/tools/<name>/validators.py` — what makes the output correct.
4. `tests/contract/samples.py` — one line. *(once plan 02 lands)*
5. `tests/unit/test_<name>.py` — only what is specific to this tool.
6. `src/docmax/cli/commands.py` — a command. **Temporary.** Plan 01 deletes
   this step; until then, copy the shape of the neighbours exactly and add
   nothing per-tool beyond argument names.

Apart from step 6, you should not need to touch `cli/`, `server/`, or any file
shared with another tool. **If you do, that is the bug** — the missing
generality belongs in the spec or the router, not in a special case.

## Where decisions live

- `docs/adr/` — accepted decisions and why. Add one rather than reversing a
  constraint quietly.
- `docs/plans/` — decided but unwritten work. Delete a plan when it lands; its
  decision should have become an ADR and its rule a test.
- `docs/architecture/` — the layering and the structural guarantees.
- `docs/planning/` — where the project currently is. Not the same as `plans/`.

## Style

Match the surrounding code. This repo writes long, specific module docstrings
that explain *why* — usually by naming the v2 failure the design prevents. That
is deliberate; keep it up. Comments explain reasons, not mechanics.
