# ADR 0039 — `docmax mcp connect` writes a client's config file directly

**Status:** Accepted · 2026-09-10

## Context

`docmax mcp` (ADR 0027) has served the tools over stdio JSON-RPC since M10.
Nothing in the codebase has ever made a client actually *start* it: the
README and `docs/implementation/mcp.md` show a copy-pasteable JSON snippet
and leave the rest to the person reading it — find the right config file for
their client, understand `mcpServers`, and merge the entry in without
disturbing whatever else is already there. Getting any part of that wrong
fails silently: the client simply never offers DocMax's tools, with nothing
to look up, because from DocMax's side the client never even ran it.

The same gap exists for the remote bridge (ADR 0035): a bearer token and a
URL are all a client needs, but there is no step that turns "I ran `docmax
cloud login`" into "my client can reach it."

## Decision

`docmax mcp` becomes a command group. `docmax mcp` with no subcommand is
unchanged — it still serves stdio. `docmax mcp connect` is new, and it
**writes to a client's own configuration file**, not to anything DocMax owns.

Scope is deliberately narrow:

- Only clients whose config shape is documented and stable are handled:
  Claude Desktop and Cursor's stdio `mcpServers` file, and Claude Code's
  project-scoped `.mcp.json`, which alone gets `--remote` support (its
  `{"type": "http", "url": ..., "headers": ...}` shape is documented; the
  others' remote-server shapes are not confirmed here, so `connect --remote`
  skips them and prints instructions instead of guessing).
- A client only becomes a target if there is evidence it is actually on the
  machine — its config *directory* already exists. `connect` never creates an
  application's directory tree for an app that was never installed. The one
  exception is Claude Code's project file, which is inert until the client is
  pointed at that directory, so it is always offered (subject to the same
  plan-then-confirm gate as everything else).
- The write is a merge, not a replace: only `mcpServers.docmax` is touched.
  Every other server entry and every other top-level key in the file is
  carried through unchanged. A file that is not valid JSON, or not a JSON
  object, is left alone and reported rather than clobbered.
- Every write goes through `core/atomic.py`, exactly like every other write in
  this codebase (rule 4) — the file being a third party's rather than
  DocMax's own changes nothing about the crash-mid-write failure mode.
- The same plan-then-confirm shape as `docmax setup`: a dry run and a
  `--yes` are both available, and refusing to write without either when
  there is no one to prompt is the same refusal `setup` already makes.

### The remote case and the "key never appears in output" rule

ADR 0014 states the API key never appears in output — stdout, stderr, or a
`--json` envelope. `connect --remote` writes that same key into Claude Code's
`.mcp.json` as a bearer header. This does not weaken ADR 0014: "output" there
means what the *terminal* shows, because that is what a shared log, a CI
transcript, or a screen share can capture. A second local file, on the same
disk, readable by exactly the account that could already read
`config.toml`'s `[cloud] api_key`, is not a wider disclosure — it is the same
secret, in the same trust boundary, doing the job it was stored for.

## Consequences

- `cli/mcp_group.py` holds the whole group: the unchanged `serve` behaviour
  as the group callback, and `connect` alongside it. `cli/main.py` drops its
  inline `mcp` command in favour of `app.add_typer(mcp_group.mcp_app)`,
  mirroring how `docmax cloud` is already wired in.
- A new, small table of known clients (name, config path, whether it is
  detected, whether `--remote` is implemented for it) lives in
  `cli/mcp_group.py`. This is not the per-tool table rule 1 forbids — it maps
  external applications to their own config shapes, not DocMax tools to
  anything — but it is exactly the kind of thing that grows one entry at a
  time, so a fourth client belongs here, in one place, rather than as a new
  branch anywhere else.
- Adding a client later means adding one `ClientTarget` and, if its remote
  shape is ever confirmed, flipping `supports_remote` — not a new code path.
- README.md and `docs/implementation/mcp.md` are updated to lead with
  `docmax mcp connect` and keep the manual snippet as the fallback for a
  client not on the list.

## Enforcement

- `tests/unit/test_cli_mcp_connect.py` covers: merge-only writes (an existing
  unrelated `mcpServers` entry survives), no write without `--yes` when not
  interactive, `--dry-run` starts no write, `--remote` without a configured
  API key fails with the same remedy `cloud login` already gives elsewhere,
  and a client detected only by its directory's presence.
- `tests/paths.py::LIBRARY_PACKAGES` excludes `cli` from
  `test_no_direct_writes.py`'s AST scan — deliberately, since `cli` is the
  layer allowed to exit and to print. `write_document` still routes through
  `atomic_write` anyway, the same voluntary discipline `cli/cloud.py`'s
  `_write_api_key` already follows for `config.toml`: the hygiene test does
  not require it here, but the failure mode (a crash mid-write corrupting a
  file the user did not ask to touch) does not care which package the write
  happens to live in.
- No new hygiene test is needed for rule 1 either: this is a per-*client*
  table in the interface layer (which client apps exist and where their
  config lives), not the per-tool table rule 1's existing scan forbids.
