# ADR 0011 — `convert` is Pandoc-only at M5: no PDF in, no PDF out, and no derived output path

**Status:** Accepted · 2026-08-26

## Context

M5 delivers `convert`. The repository declares exactly one dependency for it —
`_binaries.py`:

```python
Binary(name="pandoc", used_by=("convert",), install={...})
```

`doctor` has printed that row since M0. Building the tool turned up three
questions the documentation does not settle, and each of them would be an
awkward thing to discover in a diff.

**Pandoc cannot read PDF.** It has no PDF reader and never has. PDF is an
*output* format for Pandoc, and only when an external PDF engine is installed.
This matters because "convert a PDF to Word" is the thing a user of a PDF
toolkit will reach for first, and because it is not obvious — the tool is
otherwise famously omnivorous.

**Pandoc cannot write PDF on its own either.** It shells out to a PDF engine —
`pdflatex`, `xelatex`, `typst`, `weasyprint`. `architecture/overview.md` names
"Pandoc + a LaTeX distribution" for `convert`, but it does so in *the cloud
engine table*, whose column heading is "Local install it lets you skip", and
`cloud-api.md` says the same thing the same way: the cloud exists so you can
"convert to PDF without installing a LaTeX distribution". Both describe what the
M6 cloud engine is for. Neither is an M5 requirement, and no roadmap row asks
for `--to pdf`.

**`ToolSpec.default_suffix` is a single fixed string.** `convert`'s output
extension depends on `--to`, so a derived destination would need the suffix to
come from a parameter — a change to `core/registry.py` and `core/models.py`.
`implementation/core.md` assumes convert can derive one ("`convert x.pdf --to
pdf` derives its own input"), which is why this is a decision and not an
oversight.

## Decision

**M5's `convert` is Pandoc and nothing else. No second external binary is
added.** Not a PDF engine, and not a PDF reader — `pdftotext` ships in the same
Poppler package `to-images` already uses, so reaching for it would have been
easy and is refused for the same reason a LaTeX distribution is.

**PDF is declared in the format table as neither readable nor writable by
`convert`.** It is listed rather than omitted precisely so that both
`--to pdf` and a `.pdf` input produce a message that explains the situation and
names what to do instead, rather than "unknown format". A user who types the
obvious thing gets a real answer.

**The eight formats are** `md`, `html`, `docx`, `odt`, `rst`, `latex`, `epub`,
`txt` — every one of which Pandoc handles with no external engine. `txt` is
write-only, because Pandoc's `plain` is a writer with no matching reader.

**`convert` requires `-o`.** `default_suffix` stays `.pdf` and unexamined;
`ToolSpec` and `OutputTarget` are unchanged. The CLI makes `--output` a required
option, so the destination is always explicit and no suffix is ever derived.

The safety contract is unaffected by that choice. `OutputTarget.resolve` refuses
a destination that is any input, so the case ADR 0003 names —
`convert x.md --to md` writing over `x.md` — is still refused, now as an
explicit `-o x.md` rather than as a derived path. The refusal is
`output.in_place_overwrite` either way.

## Alternatives considered

**Add a PDF engine.** A LaTeX distribution is a multi-gigabyte install;
`typst` and `weasyprint` are smaller but are still a second binary, a second
`doctor` row, and a second thing that can be missing. Rejected: no documented
requirement asks for it, and M6's cloud engine is the answer the architecture
already gives to "I do not want to install a LaTeX distribution". Building the
local half first would make the cloud engine redundant for the exact case it was
designed for.

**Add `pdftotext` so PDF input works.** Tempting, because Poppler is already a
declared dependency for `to-images` and it would make the obvious command work.
Rejected: `pdftotext` returns a stream of words with the layout thrown away.
"Convert this PDF to Word" would produce something that opens in Word and is not
the document — which is the class of output v2 shipped and this rewrite exists to
stop. A bad conversion that looks like a good one is worse than a refusal.
Extracting text from a PDF properly is what M8's OCR work is for.

**Make `default_suffix` parameter-dependent** so `convert in.md --to docx`
derives `in.docx`. Genuinely nicer to use, and `implementation/core.md` assumes
it. Rejected for M5: it changes `ToolSpec` and `OutputTarget.resolve`, which are
the load-bearing types of ADR 0003, in the same change that adds three tools.
It is also the same shape as the unresolved `produces_output` seam — two tools
wanting a third thing from `ToolSpec` — and those should be decided together,
deliberately, rather than one at a time under feature pressure.

**Silently omit PDF from the format list.** Rejected: the user still types
`--to pdf`, and "unknown format: pdf" is a worse answer than the truth.

## Consequences

**The cost, stated plainly: `docmax convert report.pdf --to docx` does not
work, and that is the command most users will try first.** It fails with a
message naming Pandoc's limitation, pointing at `to-images` for rasterising a
PDF and at the roadmap for what is coming. This is the single largest gap in M5
and it is a deliberate one.

Also:

- `convert` is a converter *between markup and word-processing formats* — the
  operation ADR 0003 and the changelog describe (`convert x.md --to md`) — and
  not a PDF converter. The README's headline lists "convert" among PDF
  operations, which now reads more broadly than the tool delivers.
- `architecture/overview.md`'s "Pandoc + a LaTeX distribution" describes a local
  engine that does not yet exist. It is accurate about M6 and ahead of M5.
- Requiring `-o` is one more character than v2 needed. Every other v3 writing
  tool already requires it, so `convert` is consistent rather than special.

**What it buys.** Three tools land with zero new external dependencies. Every
conversion M5 offers is one Pandoc can actually perform, so there is no format
that is advertised and broken. And the two contracts that would have had to
change — `ToolSpec` and `OutputTarget` — are untouched.

## Enforcement

- `tests/unit/test_m5_tools.py` asserts `--to pdf` and a `.pdf` input are both
  refused by `convert`, with the message naming the alternative rather than
  reporting an unknown format.
- The same file asserts that `convert`'s declared formats are exactly those the
  shared table marks as convertible, so adding a format to one without the other
  fails.
- `tests/unit/test_cli_m5.py` asserts `convert` cannot be invoked without `-o`.
- `EXTERNAL_BINARIES` in `_binaries.py` is the enforcement for "no second
  binary": `doctor` renders it, and a new dependency cannot be used without
  appearing there. Nothing automatically fails a build that adds one — that is a
  review-level rule, and it is stated here so the reason is on the record.
