# Pipelines, batch and folder watch

M9. Three commands over one package, `docmax.runners`, which composes operations
the registry already knows rather than performing any itself.

The decisions behind all of this are in
[ADR 0023](../adr/0023-runners-are-a-package-below-the-interfaces.md) (where the
package lives), [ADR 0024](../adr/0024-a-pipeline-is-a-toml-file.md) (the
pipeline format), [ADR 0025](../adr/0025-batch-mirrors-names-into-an-output-directory.md)
(batch naming and failure isolation) and
[ADR 0026](../adr/0026-the-watcher-polls-and-never-watches-its-own-output.md)
(the watcher). This file describes what exists.

---

## One execution path

```
watch  ──┐
         ├──►  run_batch  ──►  run_pipeline  ──►  EngineRouter.run  ──►  strategy
batch  ──┘                          ▲
                                    │
pipeline ───────────────────────────┘
```

A batch runs a pipeline over many inputs. A watch runs a batch's shape until it
is cancelled. `--tool NAME` builds a **one-stage pipeline**, so there is no
second route to a tool and no second place that parameters are passed.

Nothing in `runners/` imports a tool. A stage names one as a string and
`EngineRouter` resolves it, which is why adding tool #51 costs the runners
nothing and why the package does not pull in pypdf.

---

## `docmax pipeline`

```bash
docmax pipeline scan.pdf --pipeline clean.toml -o clean.pdf
```

### The file

```toml
name = "scan-cleanup"

[[stage]]
tool = "ocr"
params = { lang = "eng", dpi = 300 }

[[stage]]
tool = "compress"
engine = "local"
params = { preset = "ebook" }
```

| Level | Key | Required | Meaning |
|---|---|---|---|
| top | `name` | no | What to call this run in messages and JSON |
| top | `stage` | **yes** | Array of tables, at least one |
| stage | `tool` | **yes** | A registered tool name |
| stage | `params` | no | Table of that tool's own parameters |
| stage | `engine` | no | `local`, `cloud` or `auto` for this stage |

**Any other key is an error**, naming the key — the rule `config.toml` already
follows. `params` is nested rather than flat so that a tool with a parameter
called `tool` or `engine` stays expressible.

### What runs where

Only the **last** stage writes `-o`. Every earlier stage writes into one
`TemporaryDirectory`, removed on the way out whether the run succeeded, failed,
or was cancelled. So:

- a failure at stage three leaves the destination exactly as it was,
- nothing is ever left beside the user's documents,
- the destination is written once, by `core/atomic.py`, after the final stage's
  own validators have passed.

### Validation, before anything is created

Checked against the registry first, so a typo in stage three does not cost two
minutes of stage one:

- the tool exists,
- every parameter name is one the tool declares, and every required one is given,
- a value with declared `choices` is one of them,
- no non-final stage is a tool that cannot hand a single file to the next.

That last set is two frozensets in `runners/pipeline.py`:

| Set | Members | Why |
|---|---|---|
| `NOT_A_MIDDLE_STAGE` | `split`, `to-images`, `get-info`, `permissions` | Produces a directory, or produces nothing |
| `SUFFIX_FROM_PARAMS` | `convert` | Its extension comes from `--to`, so an intermediate name cannot be derived |

Both are maintained by hand because `ToolSpec` cannot express either property.
That is the same seam recorded three times already in
[current-status.md](../planning/current-status.md) and met a fourth time by
`tui/catalog.py` at M7 — see ADR 0024's closing section.

`--dry-run` resolves each stage's engine and runs none of them.

---

## `docmax batch`

```bash
docmax batch scans/*.pdf --output-dir out --tool ocr
docmax batch scans/*.pdf --output-dir out --pipeline clean.toml
```

Exactly one of `--tool` or `--pipeline`. `--engine` goes with `--tool` only: a
pipeline file states each stage's engine, and a flag silently overriding all of
them would make the file's own answer a lie.

### Naming

Each input's **stem** is mirrored into `--output-dir` with the final tool's
`default_suffix`. `reports/q1.docx` → `out/q1.pdf`. The directory must exist;
batch does not create it.

### Refused before anything runs

Both are fatal, because neither is recoverable once it has happened:

1. **Two inputs mirroring to one name** — `a/report.pdf` and `b/report.pdf`.
2. **Any destination that is also any input in the batch** — including another
   item's input, which `OutputTarget` cannot see because it compares against the
   inputs of its own call.

### Per document

Everything else is per item and never ends the run. A corrupt file, an encrypted
one, a destination that exists without `--force` — recorded, and the batch
carries on. The typed error is kept whole rather than stringified, and the
failures are available together:

```python
group = report.as_exception_group()  # ExceptionGroup[DocMaxError] | None
```

Returned rather than raised: a batch that processed 198 of 200 documents has
done most of its job, and raising would discard it.

**Serial.** One document at a time, in the order given. ADR 0025 records why,
and what revisiting it would require.

### Exit codes

| | |
|---|---|
| `0` | every document succeeded |
| `1` | at least one document failed, or the batch itself was refused |
| `130` | cancelled |

---

## `docmax watch`

```bash
docmax watch inbox --output-dir done --tool ocr
docmax watch inbox --output-dir done --pipeline clean.toml --pattern '*.pdf' --interval 2
```

Runs until Ctrl-C.

### The rule that makes this not v2's watcher

**`--output-dir` may not be inside the watched folder, and the watched folder may
not be inside `--output-dir`.** Checked once, before the loop, and refused.

v2 wrote `_preprocessed.png` beside its input, saw it as new input, and fed on
itself. Stating the rule as containment in *both* directions rather than as
inequality is deliberate: `--output-dir inbox/done` is the mistake a user
actually makes, and plain inequality would wave it through.

### How a file is picked up

1. The folder is listed, non-recursively, matching `--pattern` (repeatable).
2. A file must show the **same size and mtime across two consecutive listings**
   before it is eligible. A document still being copied in changes on at least
   one of those and waits another tick.
3. Its content is digested with `hashlib.file_digest`. A digest already seen is
   skipped — so the same file is processed once, and an identical copy under a
   second name is not work.
4. The digest is recorded **before** the run and kept whether it succeeded or
   failed, so a permanently corrupt document is reported once rather than every
   tick forever.

Polling, from the standard library. No `watchdog` and no new dependency; ADR 0026
records the reasoning, including why the settle check would be needed even with
an event library.

### Under `--json`

**One object, written when the watch ends** — not one per document. ADR 0017
says stdout carries one object per command, and a watch has a well-defined end
and a well-defined summary, so it honours that rather than refusing `--json` the
way `docmax tui` does.

Without `--json`, each document is announced as it is handled, since a watch has
no end until the user provides one.

---

## What the JSON looks like

`pipeline` uses the ordinary success envelope, with the chain in `details`:

```json
{"ok": true, "result": {"tool": "pipeline", "engine": "local",
 "outputs": ["clean.pdf"], "duration_ms": 812, "engine_version": "pypdf/6.16.1",
 "details": {"pipeline": "scan-cleanup", "stages": ["ocr", "compress"]}}}
```

`batch` and `watch` report many documents, so `ok` is stated rather than assumed
— a batch where three of two hundred failed is neither a success nor a failure:

```json
{"ok": false, "result": {"command": "batch", "pipeline": "clean.toml",
 "stages": ["ocr", "compress"], "cancelled": false,
 "items": [{"source": "a.pdf", "ok": true, "destination": "out/a.pdf",
            "engine": "local", "duration_ms": 611},
           {"source": "b.pdf", "ok": false, "destination": null,
            "error": {"code": "input.corrupt", "message": "...", "remedy": "..."}}],
 "summary": {"total": 2, "succeeded": 1, "failed": 1}}}
```

`error` is the same envelope a single command produces, so a script parses one
shape whether it ran one document or two hundred. The stable keys are `ok`,
`result`, `items[].ok`, `items[].source` and `error.code`; `details` is
pass-through and is not contract, as
[json.md](json.md) says for every other command.

---

## Progress

`ProgressSink` has three methods and no way to say "item 7 of 200", and adding
one would be a Core change for a cosmetic reason. Instead the runners wrap the
caller's sink and prefix a label onto whatever description the tool sets:

```
[7/200] invoice.pdf [2/3] compress: Compressing 14 page(s) with Ghostscript
└─ batch          └─ pipeline stage └─ the tool's own description
```

A one-stage pipeline adds no stage label, since `[1/1] ocr:` in front of the
tool's own description is noise.

---

## Cancellation

Ctrl-C goes through the CLI's existing bridge — the same `_interruptible` that
`execute` uses, exposed as `interruptible` so there is one implementation of
signal handling — and sets the shared `CancellationToken`.

| | |
|---|---|
| pipeline | the current stage stops; the destination is untouched and the temp directory is gone |
| batch | the current document's staged file is discarded; **no further document is started**; the report carries what finished |
| watch | the loop ends on the next check; the report carries everything processed |

---

## Deferred, deliberately

**`--resume` does not exist.** The roadmap row says "resumable batch" and
`core/errors.py`'s `CancelledError` docstring says "a resumable batch records
progress so `--resume` picks up where this left off". Both are ahead of the code.

A journal is a persistent, app-owned artifact and deserves what ADR 0008 gave the
consent record — a decided location, a schema version, and a defined behaviour on
a corrupt file — rather than a format improvised inside a feature branch. It was
deferred rather than invented, and it is the one row of the M9 roadmap entry that
is not delivered.

Without it, re-running a batch repeats the documents that already succeeded.
That is safe rather than destructive: their destinations now exist, and
`OutputTarget` refuses them without `--force`.

**The watcher's processed-set is in memory**, for the same reason. A restarted
watcher reprocesses the folder, and those runs fail on `OutputExistsError`
instead of doing damage.
