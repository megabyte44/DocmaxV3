# The MCP server

M10. `docmax mcp` serves the tools over the Model Context Protocol on stdio, so
an AI agent can drive DocMax with nothing leaving the machine.

The decisions are in [ADR 0027](../adr/0027-mcp-is-an-optional-interface-behind-an-extra.md)
(the SDK, the extra, packaging, the entry point),
[ADR 0028](../adr/0028-the-mcp-tool-surface-is-the-registry.md) (what is exposed
and what the schema can say), [ADR 0029](../adr/0029-the-mcp-policy-boundary.md)
(roots, offline, consent) and
[ADR 0030](../adr/0030-mcp-cancellation-maps-onto-the-cancellation-token.md)
(cancellation). This file describes what exists.

---

## Install and run

```bash
pip install "DocmaxV3[mcp]"
docmax mcp --root ~/Documents/scans
```

The **code** ships in the wheel; only the SDK is gated by the extra. That is the
opposite of `docmax.server`, which is excluded from the wheel and deployed from a
checkout — because MCP is a command a *user* runs locally, named in their own
client's configuration.

Without the extra, `docmax mcp` prints the install line as a typed error rather
than raising `ImportError`.

A typical client configuration:

```json
{
  "mcpServers": {
    "docmax": {
      "command": "docmax",
      "args": ["mcp", "--root", "/home/you/Documents"]
    }
  }
}
```

`--json` is accepted and then refused: stdout carries the JSON-RPC stream, which
is the one thing [ADR 0017](../adr/0017-json-output-contract.md)'s single object
cannot share a channel with. `docmax tui --json` refuses for the same reason.

A bare `docmax` never starts the MCP server. That invocation belongs to the TUI
(ADR 0020), and a person typing `docmax` has not asked for a protocol server.

---

## The path a call takes

```
MCP client
  → docmax.mcp.server        one funnel: the only router call site
  → docmax.mcp.policy        roots, offline, no --force        (ADR 0029)
  → EngineRouter.run         engine resolution, consent gate
  → registry → tool          the same tool the CLI runs
  → validators → core/atomic.py
```

Nothing in `docmax.mcp` performs a document operation, and **nothing in it names
a tool**. `list_tools` is `iter_tools()` rendered through `schema.py`; adding
tool #20 makes it appear over MCP with no edit to this package.

| Module | Imports the SDK? | What it holds |
|---|---|---|
| `__init__.py` | no | `is_available`, `require_available`, `serve` — the whole entry point |
| `schema.py` | no | `ToolSpec` → JSON Schema |
| `policy.py` | no | roots, the offline rule, the boundary check |
| `server.py` | **yes** | the SDK wiring and the single router call site |

Keeping the SDK to one module is what lets the schema and the security half be
tested with no protocol session and no optional dependency — the argument
`tui/forms.py` made at M7.

---

## The tools

Every registered tool, all nineteen. Each carries a description from its
`summary` and a schema from its `ToolSpec`:

| Schema property | Comes from |
|---|---|
| `inputs` | always an array of paths; `maxItems: 1` unless `accepts_multiple_inputs` |
| `output` | optional; its description names the tool's `default_suffix` |
| one property per `Param` | `type_` → JSON type, `description`, `default`, `choices` → `enum` |
| `required` | `inputs`, plus every `Param` marked required |

`additionalProperties` is false, so an invented parameter is refused at the
boundary rather than dropped silently into `**params`.

### `output` is optional, and why

Omit it and the run is staged into a per-call `TemporaryDirectory`. A tool that
only reports — `get-info`, `permissions`, a bare `metadata` — writes nothing
there and returns its answer in `details`. A tool that *does* produce a document
is refused with a typed error naming `output`, having written it only inside the
temporary directory that is then removed.

That is how those tools stay callable without this package holding a list of
which ones produce no output. `ToolSpec` cannot say it — the first of the three
seams `current-status.md` records — and M10 does not add a field to Core.

### What is not exposed

- **`force`.** Never a parameter, so an existing file is an `OutputExistsError`
  and an agent cannot overwrite a document it did not create.
- **The M9 runners.** No `run_pipeline`, `run_batch` or `run_watch`. They are not
  registered tools, and offering them would need a hand-written list — which
  ADR 0021 and `CLAUDE.md` rule 1 forbid. `docs/plans/05` proposed one; ADR 0028
  records the contradiction and declines it.
- **Resources, prompts, filesystem browsing, shell.** The server offers tools.

---

## Security

An MCP client is a program acting on someone's behalf, and its instructions may
have come from a document it was asked to read. That is a different threat model
from a person at a shell, and it is why this section exists.

### Filesystem roots

`--root PATH` is repeatable and **defaults to the working directory**. Every path
the client sends — each of `inputs`, and `output` when given — is resolved and
must be inside a root. Anything else is refused *before execution*, with a typed
error naming the path and the roots.

Resolution before comparison is the whole check: it collapses `../../etc/passwd`
and a symlink pointing out of the tree into the same case. Component-wise
containment is used rather than a string prefix, so `/allowed-other` does not
pass because it starts with `/allowed`.

There is exactly **one** call site into `EngineRouter` in this package, and a
test asserts that, because the boundary is only a property if nothing can go
round it.

### Cloud and consent

Cloud engines are **off by default**: the server forces `offline=True` unless
`--allow-cloud` is passed. Because `offline` is one-way by design, `--allow-cloud`
does not *clear* it — a user whose `config.toml` says `offline = true` stays
offline. A flag on a protocol server cannot defeat a policy the user wrote down.

With `--allow-cloud`, a cloud route still needs consent **already recorded**.
The server reads `ConsentStore` like any other caller and **never writes it**:
`ConsentRequiredError` goes back to the client with its existing remedy, which
names `docmax cloud agree` — a command a person runs at a terminal. Consent is a
human act, and an agent that could grant it would make ADR 0008's record a
formality.

### Errors

Two rungs, kept apart on purpose:

- A **DocMax failure** — corrupt input, an existing destination, a path outside
  the roots — is `CallToolResult(is_error=true)` carrying the same
  `DocMaxError.to_dict()` envelope the CLI puts on stdout. Same codes, same
  remedies, no second taxonomy.
- A **protocol failure** — an unknown tool — is an `MCPError` on the JSON-RPC
  rung, which is the one place a separate representation is correct.

No traceback ever reaches a client: anything unexpected is already wrapped as
`InternalError` by the router, and a test asserts the payload carries no stack.

---

## Cancellation

MCP is async; DocMax tools are synchronous and blocking. The blocking call runs
in a worker thread, and the protocol's cancellation is translated into the
`CancellationToken` that already exists:

```python
token = CancellationToken()
try:
    return await anyio.to_thread.run_sync(work, abandon_on_cancel=True)
except anyio.get_cancelled_exc_class():
    token.cancel()
    raise
```

`abandon_on_cancel=True` answers the protocol at once; the token is what stops
the tool. Because it is the same token every other caller uses, **a cancelled
call leaves no partial output** — the atomic writers discard the staged file —
and that guarantee is inherited rather than rebuilt.

There is no second cancellation mechanism.

---

## Known limitations

- **No progress is reported.** A long call is silent until it returns. MCP has a
  progress notification; using it needs the request's progress token plumbed
  through and a `ProgressSink` that emits async notifications from a worker
  thread. Additive, and the most likely first complaint.
- **Input formats are not in the schema.** `ToolSpec` carries nothing describing
  what a tool can read, so `inputs` says "a path", not "a PDF". A client that
  hands `ocr` a spreadsheet learns one round trip later than a schema could have
  told it. Adding `input_suffixes` would be a Core change and a fourth open seam.
- **A TOCTOU window exists.** A path checked and then replaced with a symlink
  before the tool opens it would escape. Closing it needs `O_NOFOLLOW`-style
  handling inside every engine.
- **Calls are effectively serialised** by `anyio`'s default thread limiter. M10
  makes no concurrency claim, consistent with M9's serial batch.
- **An unimplemented tool would be offered.** `ToolSpec` still cannot say
  "declared but not implemented". `UNIMPLEMENTED` is empty today and a test holds
  it empty, so this is latent rather than live.
- **The SDK floor is verified at one point.** `mcp>=2.1,<3`, exercised against
  2.1.1 on Windows / CPython 3.14 only — the same weakness the `tui` extra has
  with Textual.
