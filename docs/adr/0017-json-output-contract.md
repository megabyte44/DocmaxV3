# ADR 0017 — `--json` is a global option, stdout carries only the envelope, and the envelope is the wire contract's

**Status:** Accepted · 2026-08-27

## Context

The roadmap promises *"`--json` everywhere"* at M6. Two pieces of it already
exist and constrain what the rest can be.

**An error envelope is already implemented.** `cli/render.py`:

```python
if as_json:
    out.print_json(json.dumps({"ok": False, "error": exc.to_dict()}))
```

`DocMaxError.to_dict()` yields `code`, `message`, `remedy`, `retryable`,
`user_fixable`, `context` — and `{"ok": false, "error": {...}}` is **exactly the
envelope `cloud-api.md` specifies for the wire.** That symmetry was deliberate
and is worth keeping: a script parsing a DocMax failure sees the same shape
whether the work ran locally or on an endpoint. `render_error(as_json=True)` is
currently never called by anything.

**The stream split is already decided**, in a comment above the two consoles:

> stderr, so that `docmax get-info x.pdf --json | jq` stays clean.

`console` is stderr and carries diagnostics; `out` is stdout and carries
results. `ConsoleProgress` writes to `console`.

**Exit codes are already argued.** `cli/execution.py` rejects a per-error-code
exit taxonomy because `ErrorCode` is already the stable machine-readable
identifier, and says *"`--json` is how a script will get one at M6"*.

What is missing: a `--json` flag anywhere, a success envelope, and a decision
about what happens to a progress bar when stdout is a pipe.

## Decision

**`--json` is a global option on the root callback**, so it is spelled the same
way for every command and a new command inherits it rather than remembering it.

**One success envelope, mirroring the error one:**

```json
{
  "ok": true,
  "result": {
    "tool": "compress",
    "engine": "local",
    "outputs": ["/abs/path/small.pdf"],
    "duration_ms": 1840,
    "engine_version": "gs/10.03.0",
    "details": { "pages": 12, "preset": "ebook" }
  }
}
```

Every field is something `ToolResult` already carries. `details` is passed
through as the tool produced it and is explicitly **not** part of the stable
contract — `ok`, `result`, `error`, and `error.code` are. That distinction is
stated in the JSON documentation so a script author knows which keys they may
depend on.

`outputs` is a list of absolute paths, always — including the empty list for a
read-only tool such as `get-info` or `permissions`, whose answer lives in
`details`. A tool that writes nothing produces `"outputs": []`, not a missing
key.

**stdout carries the envelope and nothing else.** One JSON object, one command,
one line of stdout. Under `--json`:

- every human-readable line moves to stderr
- the Rich progress bar is **suppressed entirely**, not merely redirected —
  a live-updating region on stderr is noise in a CI log, and `NullProgress`
  already exists for exactly the case of a caller that does not want progress
- tables (`formats`, `doctor`, `get-info`) are replaced by the envelope, not
  printed alongside it

**Exit codes do not change.** `0` success, `1` any anticipated failure, `2`
usage, `130` cancelled. A script reads `error.code` for detail. A usage error
from Typer's own parsing exits `2` **without** a JSON envelope, because it
happens before DocMax code runs; this is documented rather than worked around,
since making Typer emit our envelope means owning its error rendering.

**"Everywhere" means every command that runs a tool, plus `doctor` and
`formats`.** Those two produce structured answers a script would want.
`--version` and `--help` are excluded: they are not results.

## Alternatives considered

**A per-command `--json` option.** Rejected: eighteen commands, eighteen
chances to forget, and the M5 audit already found `--position`/`--allow` help
text drifting when a vocabulary lived in more than one place. The global option
is one declaration.

**A new exit code per error family.** Rejected — already rejected, in
`cli/execution.py`, for the reason that a parallel taxonomy has to be kept in
sync with `ErrorCode` and will drift. This ADR does not reopen it.

**Redirect the progress bar to stderr under `--json` rather than suppressing
it.** Tempting, since stderr is already the diagnostic stream and a human
watching a long OCR wants feedback. Rejected: Rich's live region emits cursor
control sequences, and the overwhelmingly common use of `--json` is a
non-interactive caller whose stderr is a log file. A user who wants both can run
without `--json` and read the result from the file.

**Newline-delimited JSON, one object per output.** Would suit `split` and
`to-images`, which produce many files. Rejected: it makes the common case
(`| jq .result.outputs[0]`) harder to serve the uncommon one, and `outputs`
being a list already carries the same information in one object.

**A separate `--format json|text` option.** More extensible. Rejected as
speculative: there is no third format, and `--json` is what the roadmap and
`render.py`'s own comment already name.

## Consequences

**What it costs.**

- Every renderer grows a JSON path, and every future command must go through
  the renderers rather than printing by hand. That is a convention with a test
  behind it, not a mechanism — a command calling `print()` directly would
  corrupt the stream and only an integration test would catch it.
- `details` being outside the stable contract means a script that reads
  `result.details.pages` can break at a minor version. It is documented, and
  the alternative — freezing every tool's internal counters as public API — is
  worse.
- A Typer usage error exits `2` with human text on stderr and no envelope, so a
  script must handle "not JSON" for exit code 2. Stated in the documentation.
- Suppressing progress under `--json` means a human piping to `jq` sees nothing
  happen during a long run. Accepted; the alternative pollutes CI logs.

**What it buys.** `docmax compress big.pdf -o small.pdf --json | jq -r
.result.outputs[0]` works, failures are machine-readable with a stable `code`,
and the shape is the same one the cloud API returns — so a caller writes one
parser.

## Enforcement

- A test asserts that for **every** registered command, `--json` is accepted and
  stdout parses as exactly one JSON object — parametrised over the registry, so
  a new command that forgets it fails.
- A test asserts stdout contains no ANSI escape sequences under `--json`.
- A test asserts a failing run emits `{"ok": false, "error": {...}}` on stdout
  with the expected `code`, and exits 1.
- A test asserts progress produces no stdout bytes under `--json`.
- A test asserts `outputs` is present and a list for a read-only tool, rather
  than absent.
- Nothing enforces that a future command uses the renderers instead of
  `print()`. `tests/hygiene/` has a precedent for catching that with an AST
  walk, and this is named as a gap rather than covered.
