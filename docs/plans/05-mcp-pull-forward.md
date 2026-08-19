# Plan 05 — Move MCP from M10 to M2

**Goal:** ship "drive DocMax from an AI agent, nothing leaves your machine"
while it is still a differentiator, at roughly a day of work.

---

## 1. Why it is nearly free

`ToolSpec` + `Param` are already a tool schema. `Param.type_` is a closed set of
`str | int | float | bool | path` — that is a JSON Schema generator of about
thirty lines. After [plan 01](01-spec-driven-surfaces.md), `run_tool` is the one
entry point every interface calls, so the MCP server is an adapter and not an
implementation.

The roadmap puts it last, which reads as "hardest". It is very close to the
easiest thing left, and it is the only item on the roadmap that no competitor
currently has.

## 2. Why the timing matters

"Local, private document operations an agent can call" is a strong story now
and a commodity in a year. Shipping it at M2 with five tools beats shipping it
at M10 with forty-five, because the *capability* is the news, not the tool
count — and every tool added afterwards appears in it for free.

It is also the cheapest possible proof of the layering claim: a fifth interface
over the same core, with no modification to core. That is a much better
argument for the architecture than a paragraph in `architecture.md`.

## 3. Scope

```
src/docmax/mcp/
  __init__.py
  server.py      # stdio MCP server, `docmax mcp`
  schema.py      # ToolSpec -> JSON Schema
  policy.py      # roots, consent, offline
```

- `docmax mcp` runs a stdio MCP server exposing every registered tool.
- Tool descriptions come from `summary`; input schemas from `params`,
  `accepts_multiple_inputs`, `input_suffixes`.
- Results are the [plan 04](04-json-envelope.md) envelope. Same taxonomy, same
  error codes, no new representation.
- `mcp` joins the `interfaces-are-independent` contract in `.importlinter` and
  is added to the `core-is-ui-free` forbidden list — which already names `mcp`,
  so this was anticipated.

## 4. The part that needs actual care: policy

An MCP client is a program acting on someone's behalf, which is a different
threat model from a person typing at a shell. Three rules, in `policy.py`:

**Filesystem roots.** The server accepts `--root PATH` (repeatable) and refuses
to read or write outside those roots. Default is the current directory. Without
this, a prompt-injected agent can ask DocMax to read anything the user can
read. This is the one genuinely new piece of design in the plan and it should
not be skipped or deferred.

**Cloud is off unless explicitly enabled.** `docmax mcp` defaults to
`offline=True`. An agent must not be able to trigger an upload of a user's
document, and the existing per-tool consent prompt cannot be answered by a
non-interactive client. Opt in with `--allow-cloud`, and even then only for
tools whose consent was already granted interactively.

**Nothing destructive without a real destination.** `--force` is not exposed as
a tool parameter. An agent overwriting a file it did not create is the failure
everyone will remember.

## 5. Contract-suite additions

- every registered tool produces a valid JSON Schema
- a path outside the configured roots is refused with a typed error
- cloud engines are unreachable in the default configuration
- `docmax mcp --help` imports neither `pypdf` nor `cv2`

## 6. Acceptance

- [ ] `docmax mcp` serves every registered tool over stdio
- [ ] a real MCP client can list tools and run `merge` end to end
- [ ] reads and writes outside `--root` are refused
- [ ] cloud engines are unavailable without `--allow-cloud`
- [ ] `lint-imports` covers `docmax.mcp`
- [ ] the roadmap in the README is updated — M10 becomes "pipelines, batch, watch"
