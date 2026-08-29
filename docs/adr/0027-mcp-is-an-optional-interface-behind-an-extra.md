# ADR 0027 — MCP is an optional interface, behind the `mcp` extra, started by the CLI

**Status:** Accepted · 2026-08-29

## Context

M10 adds a fourth interface: a local MCP server so an agent can drive DocMax
without anything leaving the machine. Three packaging questions arrive with it,
and the repository has already answered two of them for other interfaces.

**The SDK is not small.** Installing the official `mcp` package pulls
`jsonschema`, `sse-starlette`, `httpx2`, `httpcore2`, `opentelemetry-api`,
`pyjwt`, `attrs`, `referencing`, `rpds-py`, `truststore` and — on Windows —
`pywin32`. Non-negotiable #3 says `pip install DocmaxV3` gets a terminal tool
and nothing else, which is why `textual` is the `tui` extra and why FastAPI is
the `server` extra.

**Something has to start it.** `interfaces-are-independent` forbids the CLI from
importing another interface, and `docmax mcp` is the way in.
[ADR 0020](0020-tui-entry-point.md) met exactly this at M7 and answered it with
one narrowly spelled ignored import.

**`docs/plans/05-mcp-pull-forward.md` assumes an SDK but names no package and no
version.** It also assumes plan 01's `run_tool`, which was never built under
that name — `EngineRouter.run` is its equivalent and predates the plan's
reconciliation.

## Decision

**The dependency is `mcp>=2.1,<3`, in a new optional extra called `mcp`.**

The floor is not chosen from memory. `mcp` **2.1.1** was installed into this
project's virtualenv and its API read directly before any code was written, and
2.x is a different API from the 1.x most documentation still describes:

| What the code uses | Verified in 2.1.1 |
|---|---|
| `mcp.server.lowlevel.Server` | takes `on_list_tools` / `on_call_tool` **callbacks in its constructor**, not decorators |
| `mcp.server.stdio.stdio_server()` | async context manager yielding read/write streams |
| `types.Tool` | fields `name, title, description, input_schema, output_schema, …` (snake_case in Python) |
| `types.CallToolResult` | fields `content, structured_content, is_error, …` |
| `ServerRequestContext` | a plain dataclass — **no** `cancel_requested`; see [ADR 0030](0030-mcp-cancellation-maps-onto-the-cancellation-token.md) |

The upper bound is `<3` because 1.x → 2.x renamed and restructured the surface
above, and there is no reason to believe 3.x will not. The floor is `2.1` rather
than `2.1.1` because nothing here depends on a patch fix.

**`docmax.mcp` ships in the wheel.** This is the point where MCP differs from
the server, deliberately. `docmax.server` is excluded from the wheel because it
is deployed from a checkout inside an image that also carries Ghostscript,
Tesseract and Pandoc. `docmax mcp` is the opposite: a command a *user* runs on
their own machine, named in their MCP client's configuration. Shipping the code
and gating only the dependency is what makes `pip install "DocmaxV3[mcp]"` the
whole install story.

**The CLI starts it, through one narrowly spelled ignored import**, extending
ADR 0020's mechanism rather than inventing a second one:

```ini
ignore_imports =
    docmax.cli.main -> docmax.tui
    docmax.cli.main -> docmax.mcp
```

`docmax.mcp`'s package root exposes exactly three things — `is_available`,
`require_available`, `serve` — mirroring `docmax.tui`'s three. Anything
importing `docmax.mcp.server`, `.schema` or `.policy` from the CLI is the CLI
reaching into another interface's internals, stays broken, and is asserted
separately.

**Without the extra, `docmax mcp` reports the install line** rather than raising
`ImportError`, exactly as `docmax tui` does without `textual`.

**A bare `docmax` never starts the MCP server.** ADR 0020 gave the bare
invocation to the TUI on three guards; MCP is stdio JSON-RPC and a person typing
`docmax` has not asked for a protocol server.

## Alternatives considered

**A base dependency.** Rejected on non-negotiable #3. Eleven transitive packages
including a web-server stack, for a feature most users of a PDF tool will never
invoke.

**Excluding `docmax.mcp` from the wheel, as the server is.** Rejected: the
server is deployed, MCP is *run locally by the user*. Excluding it would mean
the documented command does not exist after `pip install`, which is not a
packaging decision but a broken feature.

**Vendoring the protocol.** MCP is JSON-RPC over stdio and a minimal server is
writable by hand. Rejected: the protocol is versioned and evolving, the SDK
carries the handshake, the dispatcher and the cancellation semantics, and
reimplementing those is how a subtle incompatibility ships. This is the opposite
case from [ADR 0019](0019-picker-package-and-rendering.md), which declined to
vendor pdf.js — there the dependency bought rendering, here it buys *protocol
conformance*, which is the whole product.

**Pinning `mcp==2.1.1`.** Rejected as too tight for a patch release, and it is
the floor that carries information anyway.

## Consequences

- **The verified floor is one version on one platform.** `2.1.1`, Windows,
  CPython 3.14. `>=2.1,<3` is a claim about a range that has been exercised at
  exactly one point in it. Named here because the `tui` extra has the same
  weakness and `current-status.md` already says so about Textual.
- **`docmax mcp --help` must not import the SDK**, or the extra becomes
  mandatory in practice. The import is inside the command body, as
  `docmax tui`'s is.
- **`mcp` is already in `HEAVY_MODULES`** (listed at M0 "before it exists"), so
  a bare `import docmax` pulling in the SDK is already a build failure. The
  guard was placed for this day and needs no change.
- **The package name collides with the SDK's.** `docmax.mcp` and `mcp` are
  different modules and absolute imports keep them apart, but the collision is
  real and a reader will trip on it. `schema.py` and `policy.py` import no SDK
  at all, which limits the surface where it matters to `server.py`.
- **One more extra to keep in `all`.** It is deliberately *not* added: `all` is
  the document-processing extras, and installing every tool dependency should
  not install a protocol server. `server` is out of `all` for the same reason.

## Enforcement

- `tests/unit/test_m10_packaging.py::test_the_wheel_ships_the_mcp_package` —
  asserts `docmax.mcp` is **not** in the `packages.find` exclusion list, the
  mirror image of `test_wheel_excludes_server.py`.
- `::test_the_mcp_extra_exists`, `::test_the_extra_is_not_in_all` and
  `::test_the_dependency_is_bounded_at_both_ends` — the extra exists, pins
  `>=2.1,<3`, and is absent from `all`.
- `::test_the_installed_sdk_satisfies_the_floor` — the version the suite
  actually ran against, asserted rather than assumed.
- `::test_importing_the_package_root_needs_no_sdk` and
  `::test_the_help_text_does_not_import_the_sdk` — both in a subprocess, since
  the rest of the suite has the SDK in `sys.modules`.
- `tests/hygiene/test_no_heavy_imports.py` — `mcp` is in `HEAVY_MODULES`, so a
  module-scope SDK import anywhere reachable from `import docmax` fails.
- `.importlinter` — `docmax.mcp` joins the layers contract and
  `interfaces-are-independent`, with one ignored import pair.
- `tests/unit/test_m10_mcp.py::test_the_cli_reaches_only_the_mcp_entry_point`
  and `::test_mcp_imports_no_other_interface` — both directions, named per file.
