# ADR 0028 — The MCP tool surface is the registry, and the schema says only what `ToolSpec` can

**Status:** Accepted · 2026-08-29

## Context

`Param`'s docstring has said since M0 what M10's share of the registry is:

> The CLI turns these into options, the TUI into form fields, the API server
> into request validation, and the M10 MCP server into a JSON schema.

[ADR 0021](0021-the-tui-is-generated-from-the-registry.md) then made that
binding for the TUI — no per-tool code anywhere in `docmax.tui` — and rejected
"filter by hand to the tools the CLI happens to expose" as *"the drift the
registry exists to prevent"*. `CLAUDE.md`'s first working rule says the same
thing and names the MCP adapter explicitly.

Two things in `docs/plans/05-mcp-pull-forward.md` cannot be built as written.

**It assumes `input_suffixes` on `ToolSpec`.** That field does not exist.
`ToolSpec` carries `name, summary, category, module, supported_engines, params,
accepts_multiple_inputs, default_suffix, aliases` and nothing describing what a
tool can *read*.

**It proposes an MCP `run_pipeline`** (echoed by
[ADR 0023](0023-runners-are-a-package-below-the-interfaces.md), which anticipated
one). Pipelines, batch and watch are **not registered tools** — they are
`docmax.runners`, composition over the registry — so exposing them means
hand-writing a tool list, which is the thing ADR 0021 and CLAUDE.md rule 1
forbid.

## Decision

**`list_tools` is `core.registry.iter_tools()`, rendered.** There is no list of
tool names anywhere in `docmax.mcp`. Adding tool #20 makes it appear over MCP
with no edit to this package, which is the same property the TUI has.

**The input schema is generated from `ToolSpec`, and says only what `ToolSpec`
can say:**

| Schema property | Comes from |
|---|---|
| `inputs` | `accepts_multiple_inputs` — an array of paths, `minItems: 1`, or exactly one |
| `output` | **optional**; described using `default_suffix` |
| one property per `Param` | `type_` → JSON Schema type, `description`, `default`, `choices` → `enum` |
| `required` | `Param.required`, plus `inputs` |

`Param.type_` is a closed set of five — `str`, `int`, `float`, `bool`, `path` —
precisely so every consumer can render it, and the mapping is four lines.

**`input_suffixes` is not added to `ToolSpec`.** M10 does not change Core. The
schema therefore constrains inputs to "a path", not "a PDF", and a client that
hands `ocr` a spreadsheet gets `UnsupportedFormatError` from the tool — a typed
error with a remedy, one round trip later than a schema could have caught it.
That is the cost, and it is smaller than the alternative: this would be the
**fourth** open `ToolSpec` seam, joining the three that
[current-status.md](../planning/current-status.md) says *"should be decided
together"*, and M9 has just added pressure to two of them. Deciding four Core
contracts is a milestone's work and would make M10 a Core change, which
[phases.md](../planning/phases.md) says to treat as a finding.

**`output` is optional, and a tool that needs one and did not get one fails
without writing anything.** When `output` is omitted the run is staged into a
per-call `TemporaryDirectory`; if the tool produced a file there, the call
returns a typed error naming `output` as required, and the temporary directory
is discarded. When `output` is given, it goes through `EngineRouter.target_for`
like every other destination.

This is what lets `get-info`, `permissions` and a bare `metadata` — the tools
behind the *first* `ToolSpec` seam, "this tool produces no output" — be called
naturally, without `docmax.mcp` holding a list of which tools those are.
`cli/execution.py` needed `execute_read_only` and a per-tool decision in
`commands.py` to do the same job; MCP may not, so it pays a wasted run on a
mistake instead.

**`force` is not exposed as a tool parameter**, per plan 05 §4: *"An agent
overwriting a file it did not create is the failure everyone will remember."*
Every MCP call runs with `force=False`, so `OutputExistsError` is reachable and
overwriting is not.

**The M9 runners are not exposed.** No `run_pipeline`, no `run_batch`, no
`run_watch`. Recorded as a finding rather than built: they are not in the
registry, and the only way to offer them is the hand-written list this ADR and
ADR 0021 exist to forbid.

**Results reuse the M6 envelope.** A successful call returns the
[ADR 0017](0017-json-output-contract.md) success payload as
`CallToolResult.structured_content`, with a short human line in `content`. An
anticipated failure returns `is_error=True` and the **same `DocMaxError.to_dict`
envelope** the CLI puts on stdout — same codes, same remedies, no second
taxonomy. A protocol-level failure (unknown tool, malformed params) is an
`MCPError` and stays on the protocol's own rung, which is the one place a
separate representation is correct.

## Alternatives considered

**A hand-written list of MCP tools.** What plan 05 implies for `run_pipeline`,
and what v2 did for its interactive menu — *"a hardcoded if/elif chain … one
entry had been renamed, raising ImportError and killing the whole session"*.
Rejected on the same grounds ADR 0021 rejected it.

**Add `input_suffixes` to `ToolSpec` as plan 05 assumes.** The right long-term
answer and a Core change. Deferred as a finding, with the pressure written down,
exactly as ADR 0021 deferred `implemented`.

**Expose the runners through a `ToolSpec`-shaped wrapper** so they enter the
registry. Rejected as out of M10's scope and wrong in itself: registering a
pipeline as a tool would make `docmax pipeline` a tool too, and the registry
would stop meaning "one document operation".

**Require `output` for every tool.** Simpler, and it makes an agent invent a
destination for `get-info` that is never written. Rejected as a worse contract
for the caller, when the temporary-directory rule costs one wasted run only on a
mistake.

**Let an omitted `output` default beside the input**, as `OutputTarget.resolve`
does for the CLI. Rejected outright: an agent that did not name a destination
must not get a file written next to the user's document. That is the M9 watcher
defect in another costume.

## Consequences

- **The schema is weaker than a hand-written one would be.** No format
  constraints on inputs, no per-tool examples. A client sees "path" where "a
  PDF" would help it.
- **`get-info` over MCP runs the tool to discover it wrote nothing** only in the
  case where a *writing* tool was called without `output`. Read-only tools cost
  nothing extra.
- **A tool with a required parameter is fully described**, including `enum`
  values, so the common class of mistake is caught by the client before a call.
- **Nothing in `docmax.mcp` names a tool**, so the package cannot rot the way
  v2's menu did — and `schema.py` imports no SDK, so the interesting half is
  testable without a protocol session at all, which is `tui/forms.py`'s argument
  repeated.
- **An unimplemented tool would be offered.** `ToolSpec` still cannot say
  "declared but not implemented" — the seam ADR 0021 named fourth and M8 closed
  by shipping `ocr`. Today `UNIMPLEMENTED` is empty and a test holds it empty,
  so this is latent rather than live, and it is the same gap in a second
  interface.

## Enforcement

- `tests/unit/test_m10_mcp.py::test_the_mcp_package_names_no_tool` — an AST scan
  for tool-name string literals in `docmax/mcp/`, the direct analogue of the
  TUI's test.
- `::test_every_registered_tool_is_offered` — `list_tools` and the registry
  agree, so a new tool appears without an edit.
- `::test_the_runners_are_not_exposed` — `pipeline`, `batch` and `watch` are
  absent, holding this ADR's finding rather than leaving it to memory.
- `tests/unit/test_m10_schema.py` — one valid JSON Schema per registered tool,
  parametrised over the registry; `enum` from `choices`; `required` from
  `Param.required`; `inputs` shaped by `accepts_multiple_inputs`; and **no
  `force` property on any tool**.
- `::test_a_tool_that_writes_and_gets_no_output_is_refused_having_written_nothing`
  and `::test_a_tool_that_reports_needs_no_output` — the two halves of the
  optional-`output` rule.
