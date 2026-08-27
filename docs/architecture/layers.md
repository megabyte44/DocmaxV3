# The layers

Four layers, each defined by what it is allowed to know about. For the rules as
CI enforces them, see [dependencies.md](dependencies.md); this document explains
what each layer is *for*.

Status is stated per layer, because several are planned rather than present.
See [current-status.md](../planning/current-status.md) for the live picture.

---

## Interfaces — `cli`, `tui`, `server`, `mcp`

**Status:** `cli` partial · **`server` implemented** (routes, jobs, storage, auth;
its tool-execution bridge is stubbed pending real tools) · `tui` M7 · `mcp` M10

The entry points. Each turns some external protocol — argv, keystrokes, HTTP,
JSON-RPC — into a call on the layers below, and turns the result back into
whatever that protocol expects.

**Responsibility**

- parse and validate input at the boundary
- render results and errors in the idiom of the medium
- decide process exit codes, HTTP statuses, or protocol errors
- own all user-facing formatting

**May import** — anything below it.

**May not import** — another interface. They are peers; see
[Interfaces are peers](dependencies.md#interfaces-are-peers-not-a-stack).

**Must not contain** — document logic of any kind. If an interface knows how
many pages a PDF has, that knowledge is in the wrong layer.

**Contracts it provides**

`ProgressSink` is the notable one: each interface implements it differently — a
Rich progress bar, a Textual widget, a job-progress row — and nothing below
knows which. This is the boundary that lets `core` be forbidden from importing
`rich`.

**Privileges nothing else has**

The CLI is the only layer permitted to terminate the process. `test_no_sys_exit.py`
enforces the asymmetry across `core`, `tools` and `cloud_client`, because v2's
`abort()` helper — called from 64 sites inside operation modules — made those
modules unusable as a library and silently defeated every `except Exception` in
the batch runner.

The server is deliberately *not* granted that privilege: a request handler that
exits takes every other in-flight request with it.

**Where the server lives**

`docmax.server` is the reference implementation of
[the Cloud Engine contract](../cloud-api.md), and it is open and in this package
rather than in a separate gated repository — see
[ADR 0006](../adr/0006-reference-server-location.md). It runs the same
`LocalStrategy` a user runs locally, on a machine that already has the heavy
dependencies installed. That is the whole mechanism: there is one implementation
of `compress`, and the only question is whose machine it runs on.

---

## Application — `tools`

**Status:** `merge` and `ocr` exist as reference layouts — real `ToolSpec`,
`Param` and validators; their `run()` bodies are stubs until Phase 6.

One subpackage per operation, each self-registering. Adding tool #51 must not
require editing a central file, and *listing* tools must not import their
implementations — two requirements that pull against each other, which is what
[ADR 0002](../adr/0002-registry-mechanism.md) resolves.

**Layout** — convention, not configuration; there is no manifest to keep in sync:

```
tools/<name>/
  tool.py        ToolSpec — name, summary, params, supported engines
  local.py       LocalStrategy — heavy deps, offline, private
  cloud.py       CloudStrategy — thin API client (absent where cloud makes no sense)
  validators.py  output checks, run against the temp file before the swap
```

**Responsibility**

- declare what the operation is and what parameters it takes
- implement up to two interchangeable `EngineStrategy` implementations
- validate its own output

**May import** — `cloud_client`, `core`.

**May not import** — any interface. A tool that formats a table for the terminal
cannot be driven by an HTTP server.

**The dual-engine rule**

Only a handful of tools have a cloud engine, because cloud exists to eliminate
install pain and nothing else. Two do today — `compress` and `convert`, since
M6. `ocr` is declared and unimplemented until M8; `pdfa` and `remove-bg` are
named in [overview.md](overview.md#the-dual-engine-model)'s end-state table and
do not exist.

Everything pypdf-only sets `cloud = None`: uploading a document to perform a
millisecond-long pure-Python operation is slower, less private, and needs a
network.

---

## Integration — `cloud_client`

**Status:** implemented — transport, auth, idempotency, server-controlled
polling, and retries only where retrying helps.

The boundary that keeps external services from spreading. Everything about
talking to a remote endpoint lives here: transport, auth, retries, polling,
and the translation of wire responses into DocMax-owned types.

**Responsibility**

- speak the contract in [cloud-api.md](../cloud-api.md)
- convert HTTP failures into the typed hierarchy in `core.errors`
- retry what is retryable and nothing else

**May import** — `core`.

**May not import** — `tools`, or any interface.

**The rule that matters**

Provider-specific objects stop here. An `httpx.Response` never travels upward;
it becomes a `ToolResult` or a `DocMaxError` at this boundary. Otherwise the
choice of HTTP library becomes an architectural fact that every layer depends
on.

**Why it sits below `tools`**

A cloud-backed tool's `cloud.py` is a *client of* this layer. The client itself
knows nothing about any particular tool — it posts a tool name and parameters
and reads back a result.

---

## Foundation — `core`

**Status:** complete. `models`, `protocols`, `atomic`, `cancellation`,
`errors`, `branding`, `registry`, `config`, `consent` and `router`.

The domain, and nothing else. Every layer speaks the types defined here, which
is why they cannot depend on any of those layers.

**Contains**

| Concern | What it is |
|---|---|
| Domain models | `DocumentRef`, `OutputTarget`, `Engine` |
| Results | `ToolResult` — engine-agnostic, so the UI cannot tell which ran |
| Errors | the typed hierarchy rooted at `DocMaxError` |
| Progress | the `ProgressSink` protocol |
| Cancellation | a framework-independent stop signal |
| Protocols | `EngineStrategy`, `Validator` |
| Registry | lazy tool discovery |
| Router | engine resolution, consent, timing, the traceback boundary |
| Atomic writes | the only module permitted to write to a destination |

**May import** — the standard library. That is the whole list.

**May not import** — `docmax.tools`, `docmax.cli`, `docmax.cloud_client`,
`docmax.server`, `rich`, `textual`, `typer`, `fastapi`, `mcp`. All enforced.

**What core does not know**

It cannot name a single tool, cannot tell you whether a terminal is attached,
cannot make a network call, and cannot end the process. Each of those is a
deliberate absence with a test behind it.

**Why protocols rather than base classes**

The contracts in `core.protocols` are `Protocol`, so an implementation satisfies
them structurally — a strategy never imports `core` in order to *be* a strategy,
and `core` never imports an implementation in order to *use* one. That is the
mechanism underneath the whole layering; inheritance would have made every tool
import the foundation and every interface import every tool.

---

## Cross-cutting concerns

Configuration, storage, security and observability are **not** a fifth layer.
Each belongs to a specific layer and is consumed through a contract:

| Concern | Owner | Consumed as |
|---|---|---|
| Configuration | `core` | resolved settings, passed in |
| Progress | `core` | `ProgressSink` protocol |
| Cancellation | `core` | a token, passed down |
| Storage | interface layer | a protocol the interface supplies |
| Security | interface layer | validated input, before core sees it |
| Observability | to be decided | see [backlog](../planning/backlog.md) |

Treating these as a layer is the usual way a layered architecture rots: a
"utils" or "common" package that everything imports and that therefore cannot
depend on anything, until it does.
