# ADR 0002 — Tools self-register through a lazy registry

**Status:** Accepted · 2026-08-13

## Context

v2 dispatched its interactive menu through a hardcoded `if/elif` chain over
emoji display strings, backed by eight parallel `*_ACTIONS` dictionaries.
Adding a tool meant editing several central files, and getting one of them
wrong was silent: one menu entry pointed at an action key that did not exist
and printed "not implemented yet"; another imported a function name that had
been renamed, raising `ImportError` and killing the whole interactive session.

Meanwhile, every workflow module was imported eagerly at CLI startup, which
transitively imported Pillow on every `docmax --help`.

Two requirements, and they pull against each other: adding tool #51 must not
touch a central file, and *discovering* tools must not import their
implementations.

## Decision

**A `@register` decorator over a directory scan of `tools/*/tool.py`, plus
`importlib.metadata` entry points under the `docmax.tools` group for
third-party packages. Both produce lazy `ToolSpec` objects.**

A `ToolSpec` carries name, description, category, parameter schema, and
`supported_engines` — everything the CLI, the TUI palette, the `--help` output,
and the M10 MCP server need. It holds a *loader* for `local.py` and `cloud.py`,
not the modules themselves. Nothing under `tools/<name>/local.py` is imported
until the router actually resolves that engine for that call.

Adding a tool is:

```
mkdir src/docmax/tools/redact/
touch  src/docmax/tools/redact/{tool,local,cloud,validators}.py
```

No central file is edited. Ever.

## Consequences

- `import docmax` and building the full registry pull in no heavy dependency.
  Asserted in `tests/hygiene/test_no_heavy_imports.py`, which runs the check in
  a subprocess because an in-process `sys.modules` assertion proves nothing once
  another test has imported OpenCV.
- Entry points mean a third party can ship `docmax-redact` on PyPI and have it
  appear in the tool list without a fork.
- A malformed `tool.py` is a registry-load error naming the offending package,
  not a crash of the whole application.
- Cost: one indirection between "I see a tool" and "I run a tool", and a small
  amount of import machinery to test. Worth it — the alternative is v2's
  dispatch chain, which failed silently in two distinct ways in production.
