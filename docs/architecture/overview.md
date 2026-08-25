# Architecture

DocMax is a terminal-native document toolkit. This document explains how it is
put together and — more usefully — *why*, since most of the structure exists to
make specific past failures unreachable.

## Layers

```
┌──────────────────────────────────────────┐
│  cli/          tui/  (M7)                │  may print, may exit
├──────────────────────────────────────────┤
│  server/       the Cloud Engine, hosted  │  not in the wheel, nothing imports it
├──────────────────────────────────────────┤
│  tools/        one package per operation  │  self-registering, lazily loaded
├──────────────────────────────────────────┤
│  cloud_client/ thin HTTP client           │
├──────────────────────────────────────────┤
│  core/         protocols and mechanisms   │  imports nothing above it
└──────────────────────────────────────────┘
```

Three of those are where the work happens, and it is worth naming what each one
is for:

| | |
|---|---|
| **tools** | *what* the product does. One package per operation, up to two engines each. |
| **cloud_client** | how a local run reaches a remote engine. Knows the wire contract and nothing else. |
| **server** | the other end of that contract. Runs the same tools, on a machine that already has the dependencies. |

`server` sits above `tools` for the same reason `cli` does: it is an interface.
It drives the registry and the engines over HTTP instead of over argv, and
nothing below it may import it — which is what keeps `pip install DocmaxV3` free
of a web framework. It is not shipped in the wheel at all: a self-hoster runs it
from a checkout, in an image that also carries Ghostscript and Tesseract, and
everyone else never downloads it.

Two consequences worth stating plainly. The server is *not* a privileged path —
it runs the same `EngineStrategy` a user would run locally, so a cloud result
cannot quietly diverge from a local one. And the client and the server are
independent implementations of [one document](../cloud-api.md), which is the only
reason that document is worth writing down.

`core` imports nothing from `tools`, `cli`, `cloud_client`, or `server`, and
never imports `rich`, `typer`, `textual`, or `fastapi`. Progress crosses that boundary as the
`ProgressSink` protocol, which is what lets one core drive a CLI, a TUI, a batch
runner, and the M10 MCP server unchanged.

The layering is enforced by [import-linter](../../.importlinter) contracts checked
in CI, not by convention.

## The dual-engine model

Every operation is a `Tool` with up to two `EngineStrategy` implementations:

```python
class EngineStrategy(Protocol):
    def is_available(self) -> bool: ...
    def unavailable_reason(self) -> str | None: ...
    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult: ...
```

`LocalStrategy` does the work here — offline, private, heavy dependencies.
`CloudStrategy` posts to an HTTP endpoint — no local install required.

`docs` is a sequence because assembling tools (`merge`, `from-images`) take
several inputs; single-input tools read `docs[0]`. `unavailable_reason` exists so
that `NoEngineAvailableError` can name *both* reasons — an engine that can only
say "no" leaves a user with nothing to act on.

A tool contributes a `ToolSpec` — name, summary, parameters, supported engines —
and the registry holds the dotted path to its package rather than its modules.
`tools/<name>/local.py` is imported only when the router resolves that engine
for that call, which is what makes discovery cost a directory walk instead of
fifty OpenCV imports. See [ADR 0002](../adr/0002-registry-mechanism.md).

`ToolResult` is engine-agnostic. The UI layer never knows or cares which one
ran; it reads `result.engine_used` only to display a badge.

**Only 5 tools have a cloud engine**, because cloud exists to eliminate install
pain and nothing else:

| Tool | Local install it lets you skip |
|---|---|
| `ocr` | Tesseract + language packs + OpenCV |
| `compress` | Ghostscript |
| `convert` | Pandoc + a LaTeX distribution |
| `pdfa` | Ghostscript |
| `remove-bg` | an ONNX model download |

Everything pypdf-only — `merge`, `split`, `rotate`, `sanitize`, `watermark`,
`stamp`, `protect`, `unlock`, `get-info` — sets
`cloud = None`. Uploading a document to perform a millisecond-long pure-Python
operation is slower, less private, and needs a network. It would be architecture
for its own sake.

## Engine resolution

`EngineRouter.resolve()` precedence, highest first:

1. explicit call argument (`--engine local`)
2. per-tool user config (`[tools.ocr] engine = "cloud"`)
3. global default
4. `auto`

`auto` resolves as: local available → local. Local dependency missing → cloud,
**but only with recorded per-tool consent**, otherwise `ConsentRequiredError`
(which the CLI renders as a y/n prompt). No network → local. Neither → 
`NoEngineAvailableError` naming both reasons.

The router also owns everything cross-cutting, so no tool implements it twice:
`OutputTarget` resolution, `--dry-run`, cancellation, timing, and wrapping any
escaping non-`DocMaxError` in `InternalError` so no UI ever renders a traceback.

## Privacy

"Local-first and private" fails the first time a document is uploaded without
the user knowing. Four rules, enforced rather than promised:

- Consent is **per tool** and recorded. No record → the operation stops.
- `offline = true` in config makes cloud unreachable **regardless of flags**,
  including an explicit `--engine cloud`.
- Every upload prints what is sent, where, and how large, before the bytes leave.
- A test asserts no path reaches `cloud_client` without passing a consent check.

## The structural guarantees

Five hygiene tests run on every PR across all nine matrix cells. The first four
exist because the corresponding failure shipped to users in v2.

| Test | Prevents |
|---|---|
| `test_no_sys_exit.py` | Library code terminating the process. v2's `abort()` was called from 64 sites; because `SystemExit` is not an `Exception`, every `except Exception` in the batch runner failed to catch it and one missing dependency killed a 200-file run. |
| `test_no_direct_writes.py` | Any write outside `core/atomic.py`. v2 had no atomic write at all — a crash mid-write left a truncated file, and several paths overwrote the input itself. |
| `test_no_heavy_imports.py` | A heavy import at module scope. Runs in a subprocess, because once pytest has imported OpenCV an in-process check proves nothing. |
| `test_branding.py` | Brand literals outside `core/branding.py`, so a rename cannot be partial. |
| `test_wheel_excludes_server.py` | Shipped code importing `server/`, which is excluded from the wheel. The import would pass every test here and fail on a user's machine, where nothing runs. |

Plus, in CI: `lint-imports` for layering, and an `open-core` job that runs the
full suite with `src/docmax/pro/` deleted.

## Errors

Everything raises from a typed hierarchy rooted at `DocMaxError`. Each error
carries a stable `code`, a `message`, an actionable `remedy`, and the
`retryable` / `user_fixable` flags the UI uses to decide whether to offer a
retry or suggest filing a bug.

A user should never see a traceback for an anticipated condition. If they do,
that is itself a bug — something escaped the hierarchy.

See [`core/errors.py`](../../src/docmax/core/errors.py).

## Decisions

- [ADR 0001 — Python 3.11](../adr/0001-python-311.md)
- [ADR 0002 — Lazy self-registering tool registry](../adr/0002-registry-mechanism.md)
- [ADR 0003 — Atomic writes and `OutputTarget`](../adr/0003-atomic-writes.md)
- [ADR 0004 — Open-core boundary](../adr/0004-open-core-boundary.md)
- [ADR 0005 — GUI pickers over localhost](../adr/0005-gui-pickers.md)
