# ADR 0010 — A tool is declared once; interfaces are generated

**Status:** Accepted · 2026-08-20

## Context

ADR 0002 established that a tool contributes a `ToolSpec` — name, summary,
parameters, supported engines — so that discovery costs metadata rather than
imports, and so that adding tool #51 touches no central file.

`Param`'s docstring states the intent plainly: *"The CLI turns these into
options, the TUI into form fields, the API server into request validation, and
the M10 MCP server into a JSON schema. A tool that described its parameters only
in its typer signature could not do any of that."*

That was written and never implemented, and the gap has since been filled by
hand twice.

**Once with a table.** `EXTERNAL_BINARIES` appeared in `cli/main.py` four days
after ADR 0002 was accepted: a hardcoded central map from tools to their
dependencies, in the interface layer, invisible to the server. The M1–M3 stack
moved it down to `tools/_binaries.py`, which fixed the layering — `tools` sits
below `cli`, so engines and `doctor` now read one list instead of two. The
remaining coupling, `Binary.used_by` naming tools from a central list, is
narrower and partly justified: it declares tools that do not exist yet so
`doctor` can report on the whole roadmap.

**Once with commands.** `cli/commands.py` now hand-writes nine per-tool
commands. Its own docstring says each one "does the same three things and
nothing else", and the file factors out `_EngineOption`, `_ForceOption`,
`_PagesOption`, and `_DryRunOption` by hand — because those options had to mean
the same thing seven times. That factoring is the correct instinct applied one
level too low: the thing that should be shared is the whole command, not the
options inside it.

Neither was careless. Both happened because the generic path did not exist and
nothing objected to the specific one.

The project is now at ten tools with thirty-odd to come, and four planned
interfaces (CLI, TUI, HTTP, MCP). Hand-writing per-tool code in each surface is
45 × 4 opportunities for the surfaces to disagree about what a tool accepts.
Ten commands is a cheap refactor; forty is one nobody schedules.

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
