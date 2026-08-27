# ADR 0010 — Formats are declared once, and `docmax formats` reads the declaration

**Status:** Accepted · 2026-08-26

## Context

Until M5 every tool accepted exactly one format. `_pdf.py` says so in one line —
`PDF_SUFFIX = ".pdf"` — and `require_pdf` is the whole of format handling.

M5 ends that. `convert` reads and writes eight document formats, `to-images`
writes three image formats, and `from-images` reads five. That is three tools
with three different answers to "what do you accept?", and the question is now
worth asking.

Two things forced a decision rather than a code comment.

**`UnsupportedFormatError` already promises a command that does not exist.**
`core/errors.py` has carried this since M0:

```python
default_remedy = f"Run `{CLI_NAME} formats` to list what this tool accepts."
```

`grep -rn formats src/` returns exactly that one line. There is no `formats`
command. This was harmless while every tool accepted only `.pdf` — the error was
nearly unreachable, and when it did fire the message was almost self-evident.
M5 makes it both reachable and wrong, and the project's stated rule is that
every anticipated failure names the exact next step. A remedy pointing at a
command that does not exist is worse than no remedy: it sends the user to a
dead end and costs them the trust that the next remedy is real.

**A format list would otherwise be written three or four times.** The CLI needs
it for `--to`'s help text and for `formats`. Each tool needs it to validate.
Nothing would keep them in step. This is the failure `_pagespec` was written to
prevent for page selections, and `_position` and `_permissions` for M4's
vocabularies — three precedents saying the same thing.

## Decision

**One declaration, in `tools/_formats.py`, private to `tools` like its three
predecessors.** It holds a `Format` record per format: the canonical name a user
types, the file suffixes that mean it, and per-tool capability. Every consumer
reads it:

- `convert` validates `--to` and the input suffix against it
- `to-images` and `from-images` validate their image formats against it
- `docmax formats` renders it, and **holds no list of its own**

**A format's capabilities are declared per direction and per tool.** `txt` is
writable and not readable, because Pandoc has no plain-text reader. `pdf` is
declared and is neither, for the reason ADR 0011 gives. Declaring a format that
cannot be used is deliberate: it is what lets `--to pdf` produce a message that
explains, rather than "unknown format: pdf".

**The set is closed and finite.** Pandoc supports upwards of forty input formats
and sixty output formats. DocMax exposes eight. A format is on the list when
someone has decided it works and a test covers it — not because a dependency
happens to accept it.

That coverage is honest about its own depth: every declared format is exercised
against a fake Pandoc, which proves DocMax passes the right reader and writer
names and handles what comes back. Exactly one conversion is run against a real
Pandoc, behind `needs_binary`. Widening that is a matter of CI time, not of
design.

**`docmax formats` is read-only and takes no document.** It answers "what can
this tool accept?", which is a question about DocMax and not about any file.

## Alternatives considered

**Change the error message instead of building the command.** One line, no new
surface. Rejected: the remedy is right and the missing piece is the command. The
message tells the user the most useful possible thing — that a definitive list
exists and is one command away — and the cheap fix would have been deleting a
good idea because it was inconvenient. It would also leave every future format
question unanswerable.

**Let each tool own its formats.** Local, obvious, and how most tools would be
written. Rejected on the same evidence as `_pagespec`: shared vocabularies drift
apart silently, and a user who learns `--to md` for one tool must not find
another spells it `markdown`. It would also give `formats` nothing to read
without importing every tool's implementation, which ADR 0002 forbids on the
`--help` path.

**Put the declaration in `core`.** Rejected: `core` owns contracts every layer
speaks. A format table is a convenience the tool layer shares among itself,
exactly like `_pagespec`, and `core` is deliberately free of anything that knows
what a PDF or a `.docx` is.

**Derive the list from Pandoc at runtime** (`pandoc --list-output-formats`).
Rejected three times over: it requires the binary to be installed in order to
answer "what formats are supported?", it makes the answer differ between
machines, and it would expose sixty formats nobody has tested.

## Consequences

**What it costs.**

- A new module and a new CLI command — surface that has to be maintained and
  that will be asked to grow. The closed-set rule is what keeps that bounded,
  and it will feel restrictive the first time someone wants `mediawiki`.
- The declaration can drift from what the binary actually does. Pandoc could
  drop a reader in a major version and the table would not notice. Nothing
  detects this; the `needs_binary` round-trip tests are the only signal, and
  they run only in CI.
- Declaring formats that cannot be used (`pdf`) means `formats` shows rows a
  user cannot act on. That is the point, but it needs the "why not" column to
  avoid reading as a bug.

**What it buys.** One place to change when a format is added. An error message
that is true. And `--to`'s help text, `formats`, and the validation that refuses
a bad value are provably the same list, because there is only one.

## Enforcement

- `tests/unit/test_m5_tools.py` asserts that every format `convert` and
  `to-images` accept *is* the shared declaration, rather than merely agreeing
  with it — so a list hard-coded into a tool fails.
- `tests/unit/test_cli_m5.py` asserts that `docmax formats` renders every
  declared row, and — structurally, by reading the renderer's own source — that
  it contains no format name of its own. A duplicated list would agree on the
  day it was written and drift later, which is the failure this ADR exists to
  prevent, so agreement of output is not enough on its own.
- `tests/unit/test_cli_m5.py` also asserts that `UnsupportedFormatError`'s
  remedy names a command the CLI actually registers, and extends the same check
  to every error class's `default_remedy`. This is what would have caught the
  original gap. One command is still unregistered — `docmax cloud login`, which
  M6 brings — and it is listed in `PLANNED_COMMANDS` with its milestone rather
  than exempted silently; a second test fails if it ever ships without the
  entry being removed.
- `tests/hygiene/test_no_heavy_imports.py` already covers the requirement that
  `_formats.py` stays importable without pulling in a document library; it
  imports nothing but `core.errors` and `core.branding`.
- `tests/hygiene/test_branding.py` covers the other half of that: the table
  carries user-facing prose, and the product name in it comes from
  `core/branding.py` rather than being spelled out.
