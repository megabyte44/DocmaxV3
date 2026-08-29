# ADR 0026 — The watcher polls, waits for a file to settle, and may not write where it watches

**Status:** Accepted · 2026-08-28

## Context

Folder watch runs an operation on documents as they appear in a directory. v2
had one, and it had the defect this project has cited more than any other:

> `_preprocessed.png` files accumulating next to your documents, and folder-watch
> mode then ate as new input.
> — [migrating-from-v2.md](../migrating-from-v2.md)

The watcher wrote its output into the directory it was watching, saw that output
as new input, processed it, and wrote more. M8 made that impossible for OCR
specifically by keeping scratch files inside a temporary directory
([ADR 0022](0022-ocr-runs-tesseract-directly-and-skips-pages-that-have-text.md)),
but nothing yet prevents a user from simply pointing `--output-dir` at the
watched folder. `backlog.md` has said since M6 that the replacement "needs a rule
about outputs written into a watched directory". This is that rule.

Three other things are unresolved and cheap to get wrong:

- **A file appears before it is finished.** A copy, an scp, or a scanner writing
  a 40 MB PDF is visible as a zero-byte file long before it is a document.
  Processing it produces `CorruptDocumentError` for a file that is perfectly
  fine two seconds later.
- **The same file arrives twice.** A save that writes, truncates and rewrites
  looks like several events. Reprocessing is wasted work at best.
- **One bad document must not end the watch.** A watcher that exits on the first
  corrupt file is a watcher nobody can leave running.

## Decision

**The watcher polls, using the standard library.** A configurable interval,
default one second, listing the watched directory non-recursively and matching a
glob. No `watchdog`, no `inotify`, no new dependency.

**A file is processed only once it has settled.** Its size and modification time
must be identical across two consecutive polls before it is eligible. A file
still being written changes on at least one of those, and waits another tick.

**Eligible files are keyed by content digest, and a digest is processed once.**
`hashlib.file_digest` — the fourth thing [ADR 0001](0001-python-311.md) raised
the Python floor for — over the settled file. The digest set is **in memory and
per run**: it is not a journal, and restarting the watcher forgets it. That is
the same deferral [ADR 0025](0025-batch-mirrors-names-into-an-output-directory.md)
makes for `--resume`, for the same reason.

**A digest is recorded as processed whether the run succeeded or failed.** A
document that fails is not retried on the next tick. Retrying it would mean
failing every second forever, and the failure is reported once instead.

**The output directory may not be inside the watched tree, and the watched
directory may not be inside the output tree.** Checked once, before the loop
starts, and refused with a typed error naming both paths. This is the rule the
backlog asked for, and it is stated as containment in both directions rather
than as inequality because `--output-dir watched/out` is the mistake a user will
actually make.

**A failed document does not stop the watch.** The typed error is reported to
the caller's observer and the loop continues.

**Cancellation is checked between ticks and inside the sleep**, so Ctrl-C is
answered within a fraction of the interval rather than at the end of it. The
in-flight operation is cancelled through the same `CancellationToken` every other
command uses, so its atomic writer discards the staged file.

**`--json` produces one object, at shutdown**, summarising every document the
run processed. It does not stream one object per file.

## Alternatives considered

**`watchdog`.** The obvious library, and it would give sub-second latency and no
polling cost. Rejected: it is a new runtime dependency for the base install, it
delivers platform-specific event semantics that differ on Windows, macOS and
Linux — coalesced events, missing events on network shares, and a rename
reported differently on each — and every one of those differences would have to
be tested on three platforms that
[current-status.md](../planning/current-status.md#verification) says are already
unverified. Polling is slower and behaves identically everywhere, and the
stability check below is required *regardless* of which mechanism reports the
file, because neither `inotify` nor `ReadDirectoryChangesW` tells you a write
has finished.

**Streaming one JSON object per processed file.** Rejected as a contract
violation. [ADR 0017](0017-json-output-contract.md) says stdout carries one
object per command. JSON Lines would be a second output format for one command,
and a script would need to know which of the two it was reading.

**Refusing `--json` outright**, as `docmax tui` does. Rejected because unlike a
TUI, a watch *can* honour the contract — it has a well-defined end and a
well-defined summary. Refusing would be a limitation invented rather than
inherited.

**Retrying failed documents.** Rejected: a permanently corrupt file would
produce an error every tick forever, and the log it fills is how a real failure
elsewhere gets missed.

**Watching recursively.** Rejected as scope. The milestone says "folder watch",
one folder, and a recursive watch multiplies the containment rule into a subtree
question. Additive later.

**Keying the processed set on path and mtime rather than content.** Cheaper — no
read of the whole file. Rejected because it cannot tell that a file was replaced
with identical content, and because the digest is what a future journal would
have to store anyway; ADR 0001 already nominated `file_digest` for that role.

## Consequences

- **Latency is up to one interval plus one settle tick** — about two seconds at
  the default. For a watcher, that is not a cost anyone will notice; it is named
  because it is the price of not taking the dependency.
- **Every eligible file is read in full to digest it**, once, before it is
  processed. For a directory of large scans that is a real read, though small
  next to OCR of the same file.
- **A restarted watcher reprocesses everything already in the folder.** The
  digest set does not survive the process. With mirrored output names and no
  `--force`, those runs fail on `OutputExistsError` rather than doing damage —
  noisy, and safe. The fix is the journal that is deferred.
- **A file modified in place after processing is processed again**, because its
  digest is new. That is the correct reading of "new input" and it is worth
  stating, because the file's name did not change.
- **The containment rule blocks a legitimate workflow**: watching `inbox/` and
  writing to `inbox/done/` is refused. The user must put `done/` outside
  `inbox/`. Accepted deliberately — the alternative is excluding a subtree from
  the scan, which is the same feature as recursive watching and reintroduces
  exactly the failure mode this rule exists to prevent.
- **Nothing enforces the containment rule across a symlink.** The check compares
  resolved paths, so a symlink inside the watched directory that points at the
  output directory is caught; a network mount aliased under two names is not.
  Named because it is a genuine hole.

## Enforcement

- `tests/unit/test_m9_watch.py::test_an_output_directory_inside_the_watched_tree_is_refused`
  and `::test_a_watched_directory_inside_the_output_tree_is_refused` — both
  directions of the containment rule, asserting the loop never starts.
- `::test_a_file_still_being_written_is_not_processed` — a file whose size
  changes between ticks waits.
- `::test_the_same_file_is_processed_only_once` and
  `::test_identical_content_under_a_second_name_is_not_reprocessed` — the digest
  key.
- `::test_a_failing_document_does_not_stop_the_watch` and
  `::test_a_failed_document_is_not_retried`.
- `::test_cancellation_ends_the_watch` — and that it stops on the first tick
  after the token is set.
- `tests/unit/test_m9_watch.py` uses a fake clock and drives the loop tick by
  tick; no test sleeps for a real second.
