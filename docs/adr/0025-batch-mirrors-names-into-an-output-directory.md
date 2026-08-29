# ADR 0025 — Batch mirrors input names into an output directory it may not share with its inputs

**Status:** Accepted · 2026-08-28

## Context

Batch runs one operation over many documents. The operation and the routing
already exist; what has to be decided is where two hundred outputs go, and what
happens when one of them fails.

`OutputTarget.resolve` derives a default destination from `inputs[0]` by swapping
the extension. For a batch that is not merely unhelpful, it is dangerous: every
input would derive a destination from the *same* first input, so two hundred
documents would resolve to one path and overwrite each other — or, when the
suffix matches the input's own, resolve onto the input and be refused. Neither
is a batch.

Three failures from v2 bound this decision, and all three are recorded in the
repository already:

- **`batch_convert` over a directory of `.md` files wrote over its own sources**
  ([ADR 0003](0003-atomic-writes.md)). Output and input shared a directory and a
  name.
- **One missing dependency terminated a 200-file run**
  ([`test_no_sys_exit.py`](../../tests/hygiene/test_no_sys_exit.py)), because
  library code called `sys.exit` and `SystemExit` is not an `Exception`.
- **Errors were flattened to `(path, str(exc))` tuples**
  ([ADR 0001](0001-python-311.md)), losing the type of every failure, so
  "retry only the ones that timed out" was impossible to write.

## Decision

**Batch takes `--output-dir DIR` and mirrors each input's stem into it**, using
the final operation's `default_suffix` from the registry. `reports/q1.docx` with
`--output-dir out` under a PDF-producing operation becomes `out/q1.pdf`. The
directory must already exist; batch does not create it.

**Every destination goes through `OutputTarget.resolve`**, one per item, so the
in-place, already-exists, and parent-writable checks are the same ones every
other command gets. Batch implements none of them a second time.

**Two contamination checks run before any work starts, and both are fatal:**

1. **Two inputs that mirror to the same destination** — `a/report.pdf` and
   `b/report.pdf` under one `--output-dir`. Refused, naming both sources.
   Running would silently deliver one document and lose the other.
2. **Any planned destination that is also any input in the batch** — not just
   its own. `OutputTarget` compares a destination against the inputs of *its*
   call, which for a per-item resolve is one document; it cannot see that
   `out/b.pdf` is item seven's source. Refused, naming the collision.

These are fatal rather than per-item because a batch that has already rewritten
item seven's source before reaching item seven is not recoverable by skipping.

**Everything else is per item.** A document that fails — corrupt, encrypted, a
destination that exists without `--force` — is recorded and the batch continues.
The typed error is kept whole, not stringified.

**Execution is serial.** One document at a time, in the order given.

**A batch is a pipeline over many inputs.** `--tool NAME` runs that tool with its
declared defaults; `--pipeline FILE` runs the pipeline of
[ADR 0024](0024-a-pipeline-is-a-toml-file.md) over each input. There is one
execution path, and parameters are passed the one way a pipeline passes them.

**`--resume` is deferred out of M9.** No journal, no persistence, no resume key.

## Alternatives considered

**Concurrency, by threads or processes.** Rejected for M9. Nothing in the
milestone requires it, and it is not free: `CancellationToken` is documented as
thread-safe but not process-safe, `ConsoleProgress` guards one live region
against exactly the `LiveError` v2 hit with five consoles, and `ProgressSink`
has no way to describe N simultaneous items. Serial keeps all three contracts
true as written. If a measured need appears, the decision to revisit is this
one, and it will need the process-boundary work `backlog.md` already names.

**An output *template* — `--output-pattern "{stem}-ocr.pdf"`.** More flexible
and more to specify: escaping, unknown fields, a field vocabulary, and what
happens when two inputs render the same name anyway. Mirroring is the common
case and does not foreclose a template later.

**Writing beside each input** — `a/report.pdf` to `a/report.ocr.pdf`. Rejected:
it is v2's shape. It scatters output through the user's tree, and it puts new
files inside a directory that a folder watch might be watching
([ADR 0026](0026-the-watcher-polls-and-never-watches-its-own-output.md) is the
other half of this).

**Stopping at the first failure.** Rejected: it is the behaviour v2 had by
accident, and it means one corrupt file in a scanned archive costs the other
hundred and ninety-nine.

**Creating `--output-dir` when absent.** Rejected, narrowly. A mistyped
directory would then succeed and put two hundred files somewhere the user did
not mean, and finding them is harder than being told the directory does not
exist. `OutputNotWritableError` already says so with the path.

## Consequences

- **`--resume` is promised in one place and does not exist.**
  `core/errors.py`'s `CancelledError` docstring says "a resumable batch records
  progress so `--resume` picks up where this left off". That sentence is now
  ahead of the code, deliberately, and is recorded as a known limitation rather
  than quietly satisfied by an invented file format. A journal is a persistent,
  app-owned artifact and deserves the treatment ADR 0008 gave the consent
  record: decided, versioned, and failing closed — not improvised inside a
  feature branch.
- **A cancelled batch loses the current item's work and no more.** The atomic
  writers discard the staged file, the destination is untouched, remaining items
  are never started. Without a journal, re-running repeats the completed items —
  which is safe, because their destinations now exist and are refused without
  `--force`.
- **Failures are reported as an `ExceptionGroup`** when a caller wants one
  exception, honouring what ADR 0001 raised the Python floor for. The runner
  returns a report rather than raising it, because raising would throw away the
  successes; the group is available from the report.
- **Serial means a 200-file OCR batch takes 200 sequential OCR runs.** This is
  the honest cost of the decision and the most likely reason to revisit it.
- **Mirroring loses directory structure.** Inputs from three directories land
  flat in one. With check (1) above, a name collision is refused rather than
  silently flattened, so nothing is lost quietly — but a user with `2024/a.pdf`
  and `2025/a.pdf` must run two batches.

## Enforcement

- `tests/unit/test_m9_batch.py::test_two_inputs_mirroring_to_one_name_are_refused`
  and `::test_a_destination_that_is_another_items_input_is_refused` — the two
  fatal checks, each asserting nothing ran.
- `::test_one_failing_item_does_not_stop_the_batch`,
  `::test_a_failed_item_leaves_every_other_output_intact` and
  `::test_a_failed_item_writes_no_partial_output` — the isolation claim.
- `::test_the_typed_error_survives_rather_than_becoming_a_string` — the typed
  error is kept, not flattened.
- `::test_cancellation_stops_scheduling_further_documents` and
  `::test_a_document_cancelled_mid_run_writes_nothing`.
- `::test_failures_are_available_as_an_exception_group` — ADR 0001's claim.
- `::test_an_existing_output_is_one_failed_item` and
  `::test_a_missing_input_is_one_failed_item_not_a_dead_batch` — that the
  per-item checks are `OutputTarget.resolve`'s and `DocumentRef.from_path`'s,
  not reimplemented ones.
- The absence of `--resume` is enforced by nothing, which is correct: it is a
  feature that does not exist, not a rule that could erode.
