# Dependency rules

This document and [`.importlinter`](../../.importlinter) describe the same rules.
When they disagree, one of them is a bug — see [Keeping this honest](#keeping-this-honest).

## The direction

```
┌─────────────────────────────────────────────────┐
│  Interfaces      cli · tui · server · mcp       │  may import everything below
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

Three contracts run in CI via `lint-imports`, in the `lint` job. A violation
fails the pull request.

| Contract | Rule |
|---|---|
| `layers` | `cli` → `tools` → `cloud_client` → `core`, downward only |
| `core-is-ui-free` | `core` may not import `rich`, `textual`, `typer`, `fastapi`, `mcp` |
| `core-is-standalone` | `core` may not import `docmax.tools`, `docmax.cli`, `docmax.cloud_client` |

Plus two AST-based hygiene tests that enforce related boundaries:

| Test | Rule |
|---|---|
| `test_no_heavy_imports.py` | `docmax` and each `docmax.core` submodule pull in no heavy dependency, no interface framework and no cloud SDK (runs in a subprocess, one probe per module) |
| `test_no_sys_exit.py` | library packages never terminate the process |

## Not yet enforced

These rules follow from the architecture but have **no check behind them yet**.
They are listed here so the gap is visible rather than assumed closed, and they
are tracked in [the backlog](../planning/backlog.md#required).

| Rule | Blocked on |
|---|---|
| `cli` and `server` may not import each other | `docmax.server` does not exist yet |
| `docmax.server` is excluded from the user wheel | as above |
| `server` may not import `cloud_client` | as above |

Adding the layer and adding its contract belong in the **same** change. A layer
that lands without its contract is a rule that exists only in this document,
which is the state this section exists to prevent.

**Two rules moved out of this table**, because the distinction turned out to
matter: a contract naming an *external* package can be written before that
package exists, since an absent module simply never appears as an import. So
`core` is already forbidden from importing `fastapi` and `mcp`, and
`HEAVY_MODULES` already lists the web frameworks and cloud SDKs — both enforced
today, on a machine where none of them is installed.

Only contracts naming an *internal* module — the three above, all of which
reference `docmax.server` — genuinely have to wait for the layer.

That `docmax.server` belongs in this package at all — rather than in a separate
repository — is settled by
[ADR 0006](../adr/0006-reference-server-location.md), which also lists the checks
it must arrive with.

## Interfaces are peers, not a stack

`cli`, `tui`, `server` and `mcp` are siblings. None is built on another, and
none may import another.

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
| `ocr` | Tesseract bindings, OpenCV, numpy | local OCR |
| `tables` | pdfplumber, pandas, openpyxl | local table extraction |
| `images` | Pillow, img2pdf | local image conversion |
| `tui` | textual | the TUI (M7) |
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
