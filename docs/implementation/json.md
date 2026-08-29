# `--json` output

Every command takes `--json`. With it, **stdout carries exactly one JSON object
and nothing else** — no progress bar, no colour, no summary line. Diagnostics
go to stderr, where they can be read or discarded independently.

```bash
docmax compress big.pdf -o small.pdf --json | jq -r .result.outputs[0]
```

The decision and its alternatives are
[ADR 0017](../adr/0017-json-output-contract.md). This document is the contract
a script may rely on.

## Where the flag goes

Both positions work, because both are things people type:

```bash
docmax --json compress big.pdf -o small.pdf
docmax compress big.pdf -o small.pdf --json
```

## Success

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

| Field | |
|---|---|
| `tool` | The tool that ran |
| `engine` | `local` or `cloud` — the one that actually ran, never a request |
| `outputs` | Absolute paths. **Always present and always a list**, including `[]` |
| `duration_ms` | Whole operation, including DocMax's own overhead |
| `engine_version` | What did the work, e.g. `gs/10.03.0`. May be `null` |
| `details` | Tool-specific. **Not stable — see below** |

`outputs` is `[]` rather than absent for a tool that writes nothing — `get-info`,
`permissions`, a bare `metadata`, and any `--dry-run`. A missing key would make
a caller distinguish "wrote nothing" from "this version does not report that",
and those are different questions.

## Failure

```json
{
  "ok": false,
  "error": {
    "code": "output.exists",
    "message": "The output path already exists: /abs/path/small.pdf",
    "remedy": "Pass --force to overwrite, or choose a different -o path.",
    "retryable": false,
    "user_fixable": true,
    "context": { "path": "/abs/path/small.pdf" }
  }
}
```

**This is the same envelope the Cloud API returns** — see
[cloud-api.md](../cloud-api.md#errors). Deliberately: a script parsing a DocMax
failure sees one shape whether the work ran here or on an endpoint, so it writes
one parser.

`error.code` is the stable machine-readable identifier. It is documented as
public API in `core/errors.py`, and renaming one is a breaking change.

## What is stable, and what is not

**Stable:** `ok`, `result`, `error`, and every field in the table above except
`details`. And `error.code`.

**Not stable:** everything inside `details`. It is passed through as the tool
produced it, and freezing every tool's internal counters as public API would
make a page count a breaking change. Read `details.pages` if it is useful;
expect it to be able to move.

## Exit codes

`--json` changes what is printed, never what is returned.

| | |
|---|---|
| `0` | success |
| `1` | any anticipated failure — `error.code` says which |
| `2` | usage error, from argument parsing |
| `130` | cancelled |

There is deliberately no exit code per error family. `ErrorCode` is already the
project's stable identifier, and a parallel taxonomy would have to be kept in
sync with it forever — `cli/execution.py` records that argument.

**One exception, worth knowing before you script against it:** a usage error
exits `2` with human-readable text on stderr and **no JSON envelope**. It
happens inside argument parsing, before DocMax code runs, and making it emit
this shape would mean owning the argument parser's error rendering. A script
should treat "exit 2" as "not JSON".

## Progress

Suppressed entirely under `--json` — not merely redirected. A live-updating
region is noise in a CI log, and the overwhelmingly common use of `--json` is a
non-interactive caller. Someone who wants both can run without `--json` and read
the result from the file it names.

## Commands that answer a question

`doctor` and `formats` produce structured answers rather than documents, in the
same `ok`/`result` envelope so a caller parses one thing.

```bash
docmax doctor --json | jq -r '.result.binaries[] | select(.found == null) | .name'
docmax formats --json | jq -r '.result.documents[] | select(.writable).name'
```

`--version` and `--help` are excluded. They are not results.
