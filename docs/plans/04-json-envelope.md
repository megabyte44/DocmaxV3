# Plan 04 — `--json` on tool #1, not on tool #30

**Goal:** every tool emits machine-readable output from the day it exists.

M6 lists "`--json` everywhere". Adding it then means auditing thirty tools and
discovering that three of them cannot express their output in the envelope.
Adding it now costs half a day, because most of it is already written.

---

## 1. What already exists

- `ErrorCode` — a stable dotted taxonomy, explicitly documented as public API
- `DocMaxError.to_dict()` — already serialises code, message, remedy,
  `retryable`, `user_fixable`, and context
- `ToolResult` — outputs, `engine_used`, `duration_ms`, `engine_version`, details

The envelope is essentially designed. What is missing is the boundary that
prints it, and the exit codes `cli/main.py` claims to own but does not define.

## 2. The envelope

One object per invocation, on stdout, nothing else on stdout in `--json` mode.

```json
{
  "docmax": "3.0.0a7",
  "ok": true,
  "tool": "merge",
  "engine": "local",
  "engine_version": "pypdf/4.2.0",
  "duration_ms": 812,
  "outputs": ["/abs/path/combined.pdf"],
  "details": {"pages": 12}
}
```

```json
{
  "docmax": "3.0.0a7",
  "ok": false,
  "tool": "merge",
  "engine": null,
  "error": {
    "code": "output.exists",
    "message": "The output path already exists: /abs/path/combined.pdf",
    "remedy": "Pass --force to overwrite, or choose a different -o path.",
    "retryable": false,
    "user_fixable": true,
    "context": {"path": "/abs/path/combined.pdf"}
  }
}
```

Rules:

- `ok` is always present and always the first thing a caller checks.
- Paths are absolute. A caller does not know the process's cwd.
- Progress and log output go to **stderr**, always, so stdout stays parseable.
- `docmax` carries the version, so a consumer can detect envelope changes.
- `details` is free-form per tool and explicitly *not* part of the stable
  contract; the top-level keys are.

## 3. Exit codes

Derived from the `ErrorCode` family prefix, so a new member never needs a new
mapping entry.

| Code | Meaning | Family |
|---|---|---|
| 0 | success | — |
| 2 | bad input or parameters | `input.*` |
| 3 | output refused | `output.*` |
| 4 | a dependency is missing or failed | `dependency.*` |
| 5 | no engine could run this | `engine.*` |
| 6 | cloud failure | `cloud.*` |
| 70 | internal error — this is our bug | `internal` |
| 130 | cancelled | `cancelled` |

70 and 130 follow convention (`EX_SOFTWARE`, 128+SIGINT). Document this table
in the README; it is part of what "scriptable" means.

## 4. Where it lives

`cli/render.py` gains a `JsonRenderer` alongside the existing rich console, and
the single error boundary from [plan 01](01-spec-driven-surfaces.md)
(`cli/errors.py`) chooses between them. Because commands are generated,
`--json` appears on every tool automatically and forever — there is no per-tool
work at all.

The server's error responses should be the same `error` object, unwrapped. Two
representations of one taxonomy is one too many, and `server/errors.py` already
exists to convert.

## 5. Contract-suite additions

Fold into [plan 02](02-contract-test-suite.md), parametrized over every tool:

- `--json` output is valid JSON and nothing but JSON on stdout
- `ok` is present; `ok: false` implies a well-formed `error` object
- the exit code matches the error family per the table above
- every `ErrorCode` member has an exit code (a test over the enum, so an added
  member cannot be forgotten)
- no stdout write happens outside the renderer when `--json` is set

## 6. Acceptance

- [ ] `docmax merge a.pdf b.pdf -o out.pdf --json` emits the success envelope
- [ ] every failure path emits the error envelope and the right exit code
- [ ] progress and warnings appear on stderr, never stdout
- [ ] the API server returns the identical `error` object shape
- [ ] the exit-code table is documented in the README
