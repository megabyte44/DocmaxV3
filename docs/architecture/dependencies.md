# Dependency rules

This document and [`.importlinter`](../../.importlinter) describe the same rules.
When they disagree, one of them is a bug — see [Keeping this honest](#keeping-this-honest).

## The direction

```
┌─────────────────────────────────────────────────┐
│  Interfaces      cli · tui · server · mcp       │  may import everything below
├─────────────────────────────────────────────────┤
│  Support         pickers · runners              │  may import tools, core
├─────────────────────────────────────────────────┤
│  Application     tools                          │  may import cloud_client, core
├─────────────────────────────────────────────────┤
│  Integration     cloud_client                   │  may import core
├─────────────────────────────────────────────────┤
│  Foundation      core                           │  imports nothing internal
└─────────────────────────────────────────────────┘
```

Dependencies point **downward only**. There is no upward import anywhere, and no
sideways import between two interfaces.

The rule that does the most work is the last line: `core` imports nothing from
the rest of the package and no UI framework. That is what lets one core drive a
CLI, a TUI, a batch runner, an HTTP server and an MCP server without
modification — each of them is a *driver* of core, not a layer core knows about.

## Currently enforced

**Five** contracts run in CI via `lint-imports`, in the `lint` job. A violation
fails the pull request.

| Contract | Rule |
|---|---|
| `layers` | `cli` → `tui` → `server` → `pickers` → `runners` → `tools` → `cloud_client` → `core`, downward only |
| `core-is-ui-free` | `core` may not import `rich`, `textual`, `typer`, `fastapi`, `mcp` |
| `interfaces-are-independent` | `cli`, `tui` and `server` may not import each other |
| `core-is-standalone` | `core` may not import `tools`, `cli`, `tui`, `pickers`, `runners`, `cloud_client`, `server` |
| `server-is-not-a-client` | `server` may not import `cloud_client`, `cli` or `tui` |

`cli`, `tui` and `server` appear on separate lines in `layers` only because a
layers contract is a *total order* and has to put one somewhere. They are peers,
and `interfaces-are-independent` is what actually forbids traffic between them.

**`pickers` is not an interface.** It sits below all three because both terminal
interfaces reach for it and neither may import the other, and it is deliberately
kept unable to print or exit so that it can be covered by the library-code
hygiene tests — which is what makes ADR 0005's "a picker never touches the
filesystem" a build failure rather than a promise. See
[ADR 0019](../adr/0019-picker-package-and-rendering.md).

**`runners` is not an interface either**, and is there for the same reason. M9's
pipelines, batch and folder watch are wanted by more than one front-end, and
`interfaces-are-independent` forbids the TUI from reaching into the CLI to get
them. Like `pickers` it never prints and never exits, so it is in
`LIBRARY_PACKAGES` and the hygiene tests cover it — which matters more here than
anywhere else, because v2's batch runner died of a `sys.exit` raised beneath it.
See [ADR 0023](../adr/0023-runners-are-a-package-below-the-interfaces.md).

**The layers contract is weaker than ADR 0023 for `runners`.** A layers contract
permits every layer below, so it allows `runners -> tools`; the ADR forbids it,
because a runner names a tool by string and lets the registry resolve it.
`tests/unit/test_m9_runners.py::test_runners_import_only_core` closes that gap by
reading the imports — an instance of the general rule below that a contract
lint cannot express.

**One import is ignored**, narrowly: `docmax.cli.main -> docmax.tui`. Something
has to start the TUI and turning argv into a call is the CLI's job; the ignored
pair reaches only the package root, whose entire surface is `is_available`,
`require_available` and `launch`. Anything importing `docmax.tui.app`, `.runner`,
`.forms` or `.catalog` still fails, and a test asserts it separately. The reverse
direction is not ignored at all. See
[ADR 0020](../adr/0020-tui-entry-point.md).

Plus two AST-based hygiene tests that enforce related boundaries:

| Test | Rule |
|---|---|
| `test_no_heavy_imports.py` | `docmax` and each `docmax.core` submodule pull in no heavy dependency, no interface framework and no cloud SDK (runs in a subprocess, one probe per module) |
| `test_no_sys_exit.py` | library packages — including `server`, `pickers` and `runners` — never terminate the process |
| `test_no_direct_writes.py` | library packages — including `pickers` and `runners` — never write outside `core/atomic.py` |
| `test_wheel_excludes_server.py` | `docmax.server` does not ship in the wheel |
| `test_tui.py` | the TUI names no tool, imports no other interface, and the CLI reaches only its entry point |

## Not yet enforced

**Nothing.** Every rule in this document has a check behind it.

This section is kept deliberately rather than deleted: it is where a rule goes
when it is written down before it can be enforced, and leaving the heading
present makes an empty list a visible claim rather than an omission.
`docmax.tui` and `docmax.pickers` arrived at M7 with their contracts in the same
change, as the architecture requires. `docmax.mcp` will do likewise at M10, and
until then there is no rule about it to leave unenforced.

## Interfaces are peers, not a stack

`cli`, `tui`, `server` and `mcp` are siblings. None is built on another, and
none may import another — with the single, narrow entry-point exception above,
which exists because a process has to start somewhere.

This needs saying because a `layers` contract is a *total order* — it has to put
one interface above another, and that ordering will read as meaningful when it
is not. When the second interface lands, the ordering inside `layers` becomes an
artefact of the contract format, and an `independence` contract is what actually
forbids traffic between them.

The direction that matters most is `cli → server`: without a rule, importing the
CLI could pull in a web framework, and `pip install docmax` would owe every user
FastAPI in order to run `docmax merge`.

## Optional dependencies

The base install is deliberately small — five pure-Python packages that install
in seconds on every platform. See [ADR 0001](../adr/0001-python-311.md) and
non-negotiable #3.

| Extra | Contains | For |
|---|---|---|
| *(base)* | `typer`, `rich`, `pypdf`, `platformdirs`, `httpx` | the shell and the cloud client |
| `ocr` | OpenCV, numpy | straightening pages during local OCR (ADR 0022) |
| `tables` | pdfplumber, pandas, openpyxl | local table extraction |
| `images` | Pillow, img2pdf | local image conversion |
| `tui` | textual | the TUI and its generated forms (M7) |
| `all` | every extra above | everything a *user* can run |
| `dev` | pytest, ruff, mypy, import-linter, … | contributors |

Two rules govern extras:

- **`all` means every document capability, not every package in the repository.**
  A dependency that a user cannot benefit from does not belong in it.
- **Extras are self-referencing** (`docmax[ocr,tables,images,tui]`) so there is
  one list rather than two that can drift. v2 shipped a `full` extra that
  silently omitted OpenCV and broke the entire OCR path for anyone who used it.

## Lazy loading and import safety

`import docmax` must stay cheap, and `docmax --help` must not import OpenCV.
Two mechanisms make that true:

1. **The registry discovers tools by metadata.** A `ToolSpec` carries a tool's
   name, summary and parameters plus the *dotted path* to its package — never
   the package itself. `tools/<name>/local.py` is imported only when the router
   has resolved that engine for that call. See
   [ADR 0002](../adr/0002-registry-mechanism.md).
2. **Heavy imports live inside functions.** A strategy answers `is_available()`
   with `importlib.util.find_spec` or `shutil.which` — never by importing the
   dependency, because availability is asked on every routing decision including
   the ones that choose the other engine.

`test_no_heavy_imports.py` asserts this in a **subprocess**. An in-process
assertion would prove nothing: once pytest or another test has imported OpenCV,
`sys.modules` reports it regardless of what `docmax` did.

## Keeping this honest

`.importlinter` is the authority; this document explains it. Whenever a contract
changes:

1. Update `.importlinter`.
2. Update the tables above.
3. If the *direction* of a dependency changed, write an ADR — that is an
   architecture change, not a configuration tweak.

If you find this document describing a rule CI does not enforce, that is a bug in
this document, not an aspiration. Either add the check or move the line to
[Not yet enforced](#not-yet-enforced).
