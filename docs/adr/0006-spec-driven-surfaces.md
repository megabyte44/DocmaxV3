# ADR 0006 — A tool is declared once; interfaces are generated

**Status:** Accepted · 2026-08-20

## Context

ADR 0002 established that a tool contributes a `ToolSpec` — name, summary,
parameters, supported engines — so that discovery costs metadata rather than
imports, and so that adding tool #51 touches no central file.

`Param`'s docstring states the intent plainly: *"The CLI turns these into
options, the TUI into form fields, the API server into request validation, and
the M10 MCP server into a JSON schema. A tool that described its parameters only
in its typer signature could not do any of that."*

That was written and never implemented. There are no generated commands, and
`cli/main.py:31` contains this:

```python
EXTERNAL_BINARIES: dict[str, tuple[str, ...]] = {
    "tesseract": ("ocr",),
    "gs": ("compress", "pdfa"),
    "pdftoppm": ("ocr", "to-images"),
    "pandoc": ("convert",),
}
```

A hardcoded central table mapping tools to their dependencies, in the interface
layer, invisible to the server — which is the exact structure ADR 0002 exists to
abolish, and the exact structure that failed in v2 as a silently-drifting
dispatch chain. It appeared four days after the ADR was accepted, because
nothing was checking.

Meanwhile the project is at one tool with forty-odd to come, and four planned
interfaces (CLI, TUI, HTTP, MCP). Hand-writing per-tool code in each surface is
45 × 4 opportunities for the surfaces to disagree about what a tool accepts.

## Decision

**A tool is declared once, in its `ToolSpec`. Every interface is generated from
the declaration. No interface may contain per-tool code.**

Concretely:

1. `ToolSpec` and `Param` are the single source of truth for a tool's name,
   summary, parameters, accepted inputs, engines, and external binaries. Facts
   about a tool live on its spec, never in a table elsewhere.
2. `core/router.py` exposes `run_tool(...)` — the one code path that resolves
   inputs, target, params, and engine, and runs the strategy. Every interface
   calls it. No interface reassembles those steps.
3. `cli/build.py` generates one typer command per registered spec.
   `server/routes/tools.py`, the TUI, and the MCP adapter build their surfaces
   the same way, from the same spec.
4. Parameter validation happens once, in `core/params.py`, so a bad value
   produces identical errors in all four surfaces.

Enforced by `tests/hygiene/test_no_handwritten_commands.py`, which AST-walks the
interface packages and fails when a command name, route segment, or dict key
matches a registered tool name. Commands that are *about* tools — `doctor`,
`setup`, `tools`, `formats`, `config`, `version`, `mcp`, `serve` — are
allowlisted explicitly in the test.

## Consequences

- Adding a tool is `tool.py` + `local.py`. The CLI command, the API route, the
  TUI form, the MCP schema, `--json`, `--force`, `--engine`, and the `doctor`
  entry all follow from the declaration. Tool #45 costs a fraction of tool #1.
- The surfaces cannot drift. `--help` disagreeing with the API becomes
  structurally impossible rather than a thing to review for.
- The MCP server stops being a milestone and becomes an adapter — see
  `docs/plans/05-mcp-pull-forward.md`.
- Cost: generating a typer signature dynamically is less obvious than writing
  one, and a stack trace through generated commands is harder to read. This is
  paid once, in one file, by the person who writes `cli/build.py`.
- Cost: the registry is now imported on every CLI startup, since the command
  tree needs every spec. That is what `ToolSpec` was designed for — metadata
  only — and `tests/hygiene/test_no_heavy_imports.py` is extended to assert
  that building the full command tree imports neither `pypdf` nor `cv2`.
- Cost: expressiveness is bounded by `Param.type_`, deliberately a small closed
  set. A tool wanting a parameter shape that no surface can render must either
  widen the set for everyone or reconsider the parameter. This is the
  constraint working, not failing.
