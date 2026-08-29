# ADR 0029 — MCP runs inside a policy boundary: roots, offline by default, and consent it may not grant

**Status:** Accepted · 2026-08-29

## Context

Every other interface is driven by a person. The CLI is someone typing, the TUI
is someone looking at a screen, the HTTP server is behind an API key its operator
issued. An MCP client is **a program acting on someone's behalf**, and its
instructions may have come from a document it was asked to summarise.

`docs/plans/05-mcp-pull-forward.md` §4 is the only security design in the
repository and names the threat exactly:

> Without this, a prompt-injected agent can ask DocMax to read anything the user
> can read. This is the one genuinely new piece of design in the plan and it
> should not be skipped or deferred.

Three existing mechanisms are load-bearing here and none of them was designed
for a non-human caller:

- **`OutputTarget.resolve`** checks in-place overwrite, existing destinations and
  a writable parent. It has no concept of an *allowed area* — every path the
  user names is legitimate, because a user named it.
- **`offline`** is one-way by design ([ADR 0008](0008-consent-record.md), Phase
  3): an override may turn it on, never off, *"because the flag exists to be
  un-overridable by the command line."*
- **Consent** is a y/n prompt the CLI raises and the TUI shows as a modal.
  `cli/execution.py` already refuses to ask when `--json` is on or stdin is not
  a terminal, *"because a prompt would either hang a script forever or corrupt
  its stdout."* An MCP client is that case permanently.

## Decision

**`docmax.mcp.policy` is a boundary every call crosses before the router sees
it.** It is an interface-layer module. **Core is unchanged.**

### 1. Filesystem roots

`docmax mcp --root PATH` is repeatable and **defaults to the current working
directory**. Every path the client supplies — each entry of `inputs`, and
`output` when given — is `Path.resolve()`d and must be inside one of the roots.
Anything else is refused before execution with a typed error naming neither the
resolved path's contents nor the roots' full layout beyond what the caller
already supplied.

Resolution before comparison is the whole check: it is what makes `../../etc`
and a symlink pointing out of the tree the same case, and it is the same
containment test M9's watcher uses ([ADR 0026](0026-the-watcher-polls-and-never-watches-its-own-output.md)).

The check happens in **one function, called from the single place that builds a
router call**. There is no second path into `EngineRouter` from this package.

### 2. Cloud is off unless asked for, and consent is never granted here

The server builds its `Config` with `offline=True` **unless** `--allow-cloud` is
passed. Because `offline` is one-way, `--allow-cloud` does not *clear* offline —
it declines to force it, and a user whose config says `offline = true` stays
offline. A flag on a protocol server cannot defeat a policy the user wrote down.

With `--allow-cloud`, a cloud route still requires consent **already recorded**.
`ConsentStore.has(tool)` is consulted through the router exactly as the CLI does,
and `ConsentRequiredError` is returned to the client as a structured error with
its existing remedy — which names `docmax cloud agree`, a command a **person**
runs at a terminal.

**`docmax.mcp` never calls `ConsentStore.record`.** Consent is a human act, and
an agent that could grant it on the user's behalf would make ADR 0008's record a
formality.

### 3. No `--force`, and no destination outside the roots

`force` is not a tool parameter ([ADR 0028](0028-the-mcp-tool-surface-is-the-registry.md))
and the server passes `force=False` always, so an existing file is an
`OutputExistsError` rather than a silent overwrite.

### 4. What is not exposed

No filesystem browsing, no shell, no MCP *resources*, no prompts. The server
offers tools and nothing else. `docmax.mcp` reads a file only by handing its
path to a tool, and writes only through `core/atomic.py` by way of one.

## Alternatives considered

**Enforce roots inside `OutputTarget`.** The tempting place, because then no
interface could bypass it. Rejected, and this is the closest call in M10: it is
a Core change, `phases.md` says to treat that as a finding, and it would put an
agent-shaped policy into the type every CLI call also uses — where the concept
does not belong, since a person typing a path has already authorised it. The
containment rule is a property of *this interface's threat model*, not of
destinations in general. Recorded so that if a second non-human caller ever
appears, the decision is revisited rather than copied.

**Default to no roots (allow everything) and require `--root` to restrict.**
Rejected. A default that is safe only if configured is not safe; the failure is
silent and total, and it lands on the user least likely to have read the docs.

**Default the root to the user's home directory.** Rejected as too wide by far —
it contains `.ssh`, `.aws`, and the DocMax config file with its API key in
plaintext ([ADR 0014](0014-api-key-storage.md)).

**Let the agent grant consent, recording it as agent-granted.** Rejected. It
inverts ADR 0008: the record exists so a human decided once, knowingly.

**Prompt through MCP elicitation.** The protocol can ask a client to ask its
user. Rejected for M10 as unproven and not universally implemented; the fallback
would have to be this behaviour anyway, and a security default that depends on
an optional protocol feature is not a default. Additive later.

## Consequences

- **The default is narrow and will surprise.** A client started in the wrong
  directory can read nothing. That is the intended failure: it is visible,
  immediate and fixed with `--root`.
- **Roots are checked at the boundary, not below it**, so this guarantee is
  `docmax.mcp`'s to keep. If a future contributor adds a second call site into
  the router from this package without going through `policy`, the boundary
  leaks. That is why the enforcement below asserts *there is one call site*
  rather than only asserting the check works.
- **A TOCTOU window exists.** A path checked, then replaced with a symlink before
  the tool opens it, escapes. Closing it needs `O_NOFOLLOW`-style handling inside
  the engines — Core, and every engine. Named because it is real, small in the
  local-agent threat model, and deliberately not fixed here.
- **Cloud over MCP is a two-step, on purpose**: install, then a human runs
  `docmax cloud agree`, then `--allow-cloud`. Nothing an agent does alone can
  upload a document.
- **The API key is never sent to a client.** Errors surface `DocMaxError.to_dict`,
  which carries code, message, remedy and context — and `test_cli_json.py`'s
  sentinel test already asserts the renderers do not leak it. M10 adds the same
  assertion against the MCP payloads.

## Enforcement

- `tests/unit/test_m10_policy.py` — a path outside the roots is refused, for
  `inputs` and for `output`; `..` traversal is refused; a symlink pointing out of
  a root is refused; the default root is the working directory; and a refused
  call **runs nothing**.
- `tests/unit/test_m10_mcp.py::test_cloud_is_off_by_default` — the server's
  config reports `offline` with no flag.
- `::test_allow_cloud_cannot_defeat_a_configured_offline` — the one-way rule.
- `::test_the_server_never_records_consent` — an AST scan for a `.record(...)`
  call anywhere in `docmax/mcp/`.
- `::test_no_force_parameter_is_offered` and
  `::test_an_existing_output_is_never_overwritten`, plus
  `::test_an_output_that_is_an_input_is_refused`.
- `::test_there_is_one_router_call_site` — an AST scan of `docmax/mcp/` for
  `EngineRouter` use, holding the "one funnel" claim.
- `::test_no_credential_reaches_the_client` — the `test_cli_json.py` sentinel,
  applied to every MCP payload.
