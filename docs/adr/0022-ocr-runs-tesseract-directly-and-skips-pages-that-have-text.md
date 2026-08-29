# ADR 0022 — OCR shells out to Tesseract directly, and leaves pages that already have text alone

**Status:** Accepted · 2026-08-28

## Context

`ocr` has been a skeleton since M0 — a complete `ToolSpec`, a documented
algorithm, a dependency list, and a `run()` that raises. Seven milestones of
architecture were built around it: it is the tool
[`architecture/overview.md`](../architecture/overview.md) uses to justify the
Cloud Engine, the tool [ADR 0018](0018-capabilities-mean-runnable.md) names as
its one unresolved residual, and the tool
[ADR 0012](0012-cloud-engines-are-compress-and-convert.md) deliberately held
back so that "the remote half is specified against operations that exist" stayed
true.

Implementing it forced two questions the skeleton did not answer, and neither
has a default that is obviously right.

**What actually runs Tesseract.** `pyproject.toml`'s `ocr` extra has declared
`pytesseract` and `pdf2image` since M0, alongside `opencv-python-headless` and
`numpy`. Both are thin subprocess wrappers around the two binaries
`tools/_binaries.py` already knows how to find, run and kill.

**What happens to a page that is already searchable.** No document in the
repository says. It is not an exotic case: a scanned contract behind a generated
cover page is ordinary, and Tesseract's PDF output *replaces* a page with its
rasterisation plus an invisible text layer.

## Decision

### Tesseract and Poppler are run through `_binaries.run`, not through wrappers

One `pdftoppm` and one `tesseract` per page, exactly as `compress` runs
Ghostscript and `to-images` runs Poppler.

`_binaries.run` is the only place in this project that guarantees a subprocess
timeout derived from the cancellation token and a kill switch registered with
`on_cancel`. Its docstring states the reason: *"v2 had no timeout anywhere and a
hung subprocess hung DocMax until it was killed by hand."* Using `pytesseract`
would have put the OCR subprocess — the longest-running in the product — outside
that guarantee, making it the sole exception on the exact axis v2 failed.

**`pytesseract` and `pdf2image` are therefore removed from the `ocr` extra.**
Nothing imports them. The extra is now OpenCV and its numpy, needed only for
`--deskew`, so `docmax ocr --no-deskew` runs with no Python extra at all.

### A page that already carries extractable text is copied through untouched

Not as an optimisation. Recognising it would be *wrong*: Tesseract's PDF output
replaces the page with a picture of itself, so re-recognising a page that has
real text discards the real text and leaves two layers where copy-paste returns
everything twice.

So the strategy partitions the document once, before any work:

- a page with ≥ `MIN_TEXT_CHARS` (16) extractable characters is copied verbatim;
- every other page is rasterised, straightened, recognised and replaced;
- the result names both sets in `details`.

Sixteen characters, not one: a scan often carries a stray glyph from a stamp or
a burned-in page number, and treating those as "already searchable" would skip
exactly the pages that most need recognising.

**A page that fails to recognise is copied through and named**, the judgement
`crop` makes about a page its box does not fit. The run fails only if *every*
page that needed recognising failed, which is the case that means something is
actually wrong.

**No new parameter.** `ToolSpec` promises `lang`, `dpi` and `deskew`, and
`--force-ocr` would be a fourth this milestone was not asked for.

### Two smaller things settled with it

**A missing language pack is refused by name.** `tesseract --list-langs` is
consulted once per run, and an uninstalled code produces a typed error listing
what *is* installed. Best-effort: a probe that fails says nothing and lets the
recognition report the problem. "exit 1" for a missing German pack sends a user
looking in the wrong place.

**`INSTALL_HINT` no longer names `docmax setup --ocr`.** That command does not
exist and is an unscheduled backlog item. The hint is now built from
`_binaries`' own per-platform declarations, so it and `doctor` cannot disagree.

## Alternatives considered

**Keep `pytesseract`.** It is declared, it is one import, and
`_engine_version()` already called it. Rejected: the version string is one
`tesseract --version` away through the mechanism every other tool already uses,
and no convenience is worth the one subprocess in the product that can run for
minutes escaping the timeout contract.

**Keep both declared but call neither.** Honest about nothing: `is_available()`
would report a package the engine does not need, making it wrong in the strict
direction, and users would install two wheels for nothing.

**One Tesseract invocation over an image-list file.** Faster — one process for a
500-page scan instead of 500. Rejected for `to-images`' stated reasons: progress
would become indeterminate, cancellation would take effect at the end of the
document rather than within a page, and per-page skipping would be impossible,
which the second decision above depends on.

**Re-OCR every page regardless.** Simplest. It silently doubles the text layer
of any document that was partly searchable, and the user discovers it when
copy-paste returns everything twice — the class of defect that is invisible in
a file that otherwise looks perfect.

**Refuse the whole document if any page has text.** Safe and wrong: it refuses
the common mixed document outright, and the user's only recourse would be to
split the file by hand.

**Add `--force-ocr` / `--redo-ocr`.** The honest way to give the user the
choice, and it invents a parameter no document in this repository promises. If
it is ever wanted, `ToolSpec` is where it goes and it is additive.

## Consequences

- **The `ocr` extra shrinks from four packages to two**, and to zero for
  `--no-deskew`. This is the project's most painful install and it got smaller.
- **A recognised page comes back as an image at `--dpi`.** That is what OCR of
  a scan means — the page was already an image — but it is a real cost and it is
  stated in `--help`, in `implementation/ocr.md`, and in the module docstring
  rather than left to be discovered.
- **A partly-searchable document is handled correctly and a fully-searchable one
  is a near no-op**, reported as `recognised: 0`.
- **One extra subprocess per run** for `--list-langs`. Worth it for a message
  that names the installed packs.
- **Two documented dependencies disappear from a published extra.** Anyone
  who installed `DocmaxV3[ocr]` for v3.0.0a7 has two wheels they no longer need.
  Nothing breaks; they are simply not used.
- Removing `pytesseract`/`pdf2image` from the mypy override list surfaced that
  modern `opencv-python` ships a `py.typed` and mypy was following it into
  numpy's stubs, which use syntax mypy rejects at this project's `python_version`.
  Fixed in the same change; noted because it means *installing the `ocr` extra*
  used to break `mypy` for a contributor and never for CI.

## Enforcement

- `tests/unit/test_ocr.py` drives the whole pipeline through *fake* `tesseract`
  and `pdftoppm` binaries that are real subprocesses, so the timeout,
  cancellation and kill-switch paths are the real ones.
  `test_a_hung_recogniser_times_out` fails if any subprocess ever escapes
  `_binaries.run`.
- `test_a_page_that_already_has_text_is_left_exactly_as_it_was`,
  `test_a_mixed_document_recognises_only_the_scanned_pages` and
  `test_a_stray_glyph_does_not_count_as_a_text_layer` pin the second decision,
  including the threshold.
- `test_one_failed_page_does_not_lose_the_others` and
  `test_a_run_where_every_page_fails_is_an_error` pin the failure rule.
- `test_nothing_is_written_beside_the_source` and its cancelled counterpart pin
  v2's `_preprocessed.png` defect.
- `tests/unit/test_deskew.py` pins the other v2 defect — see the file, and
  [ADR 0021](0021-the-tui-is-generated-from-the-registry.md) for why a pure
  function was the point.
- `test_a_missing_dependency_is_reported_with_an_install_command` asserts
  `setup --ocr` is gone.
- Nothing enforces that `pytesseract` stays uninstalled; it is simply imported
  nowhere, and `test_no_heavy_imports.py` already forbids either package
  appearing after a bare `import docmax`.
