# OCR

What `docmax ocr` does, and the rules it obeys. For *why* those rules, see
[ADR 0022](../adr/0022-ocr-runs-tesseract-directly-and-skips-pages-that-have-text.md),
and [ADR 0012](../adr/0012-cloud-engines-are-compress-and-convert.md) for why
the cloud half waited until M8.

---

## What it produces

**A searchable PDF**: the same pages, in the same order, with an invisible text
layer over the ones that needed one.

```
scan.pdf                      searchable.pdf
┌──────────────┐              ┌──────────────┐
│ [page image] │              │ [page image] │  ← re-rendered at --dpi
│  "INVOICE"   │   ──OCR──▶   │  "INVOICE"   │
│   (pixels)   │              │  + invisible │  ← selectable, searchable
└──────────────┘              │    text      │
  3 pages, no text            └──────────────┘
                                3 pages, text
```

Not a text file, not images, and not a choice of formats. `default_suffix` is
`.pdf` and the tool's whole summary is *"Add a searchable text layer to a
scanned document."*

```bash
docmax ocr scan.pdf -o searchable.pdf
docmax ocr scan.pdf -o out.pdf --lang eng+hin --dpi 400
docmax ocr scan.pdf -o out.pdf --no-deskew
docmax ocr scan.pdf -o out.pdf --engine cloud    # needs no local install
```

---

## Parameters

Three, exactly as `ToolSpec` has declared since M0.

| | Default | |
|---|---|---|
| `--lang` | `eng` | Tesseract codes; `+` combines them (`eng+hin`) |
| `--dpi` | `300` | 12–1200, shared with `to-images` via `tools/_dpi.py` |
| `--deskew` / `--no-deskew` | on | straighten pages before recognition |

Plus the universal `-o`, `--force`, `--engine`, `--dry-run`, `--json`.

`--lang eng+hin` is unchanged from v2 and
[promised by the migration guide](../migrating-from-v2.md). A pack that is not
installed is refused **by name**, with the installed ones listed — one
`tesseract --list-langs` per run buys that.

---

## The pipeline

```
scan.pdf
   │  pypdf         which pages already carry text?          ← decided once, up front
   ▼
   │  pdftoppm      one process per page, at --dpi           ← into a TemporaryDirectory
   ▼
   │  OpenCV        straighten, if --deskew                  ← tools/_deskew.py
   ▼
   │  tesseract     one process per page → single-page PDF
   ▼
   │  pypdf         reassemble, page for page
   ▼
   atomic_path + validators
   ▼
searchable.pdf
```

Every subprocess goes through `tools/_binaries.py`, which is the only place in
the project that guarantees a timeout wired to the cancellation token and a kill
switch. One process per page, for the reason `to-images` gives: per-page
progress, cancellation that lands within a page, and the ability to skip
individual pages.

---

## Pages that already carry text

**They are copied through untouched**, and this is the part worth reading before
using the tool.

Tesseract's PDF output *replaces* a page with its rasterisation plus an
invisible text layer. Re-recognising a page that already has real text would
throw the real text away and leave two layers, so a copy-paste returns
everything twice. A scanned contract behind a generated cover page is the common
case, not the exotic one.

So each page is one of three things, and the result says which:

```console
$ docmax ocr mixed.pdf -o out.pdf --json | jq .result.details
{
  "pages": 12,
  "recognised": 11,
  "skipped_with_text": [1],
  "failed": [],
  "deskewed": [3, 7],
  "lang": "eng",
  "dpi": 300
}
```

A page counts as already searchable at **16 extractable characters**
(`validators.MIN_TEXT_CHARS`). Not one: a scan often carries a stray glyph from
a stamp or a burned-in page number, and treating those as text would skip
exactly the pages that most need recognising.

**A recognised page comes back as an image at `--dpi`.** That is what OCR of a
scan means — the page was already an image — but it is a real cost, which is
why the skip above matters: a page that was text stays text.

There is no `--force-ocr`. `ToolSpec` promises three parameters and M8 did not
invent a fourth; ADR 0022 records the reasoning.

---

## Failure

| Case | Behaviour |
|---|---|
| one page fails to recognise | copied through, named in `details.failed`, run succeeds |
| **every** page fails | `dependency.tool_failed`, destination untouched |
| output has no text at all | `output.validation_failed`, naming `--lang` and `--dpi` |
| output lost or gained a page | `output.validation_failed` |
| encrypted input | `input.encrypted` — refused by `_pdf.open_pdf` before anything runs |
| corrupt input | `input.corrupt` |
| Tesseract or Poppler missing | `dependency.missing`, with the platform's install line |
| OpenCV missing and `--deskew` | `input.invalid_parameter`, suggesting `--no-deskew` |
| language pack missing | `input.invalid_parameter`, listing the installed packs |
| subprocess hangs | `dependency.tool_timeout` — per page, not per document |
| cancelled | `CancelledError`, destination untouched |

A failed page is survivable for the same reason `crop` tolerates a page its box
does not fit: losing 499 pages because of one is the wrong trade.

---

## Atomicity, and nothing beside the source

Output goes through `core/atomic.py` with both validators, so **a cancelled or
failed run leaves the destination exactly as it was** — there is no path that
writes a partial document.

Every intermediate page image lives in a `tempfile.TemporaryDirectory` that is
removed on the way out, including after a failure and after a cancellation.
v2 wrote a `_preprocessed.png` beside every document it touched, which its own
folder-watch mode then ate as new input; two tests assert the source directory
is unchanged, one of them after a cancellation.

---

## Deskew

`tools/_deskew.py`, a pure function, and the reason it is a separate module is
that v2 got it wrong in a way no test could have caught in place:

> `deskew` passed `(y, x)` points to `cv2.minAreaRect`, which expects `(x, y)`,
> and applied an angle correction written for a pre-4.5 OpenCV convention that
> never fires on current versions.

Both halves are avoided deliberately. Points come from `cv2.findNonZero`, which
returns `(x, y)`. The angle is folded into (-45°, 45°] **by modulo**, naming no
convention — because OpenCV used `(-90, 0]`, changed to `(0, 90]` at 4.5, and
reports `(-90, 0]` again at 5.0, so any code that names a range is wrong on some
install somebody has.

Angles below 0.1° are ignored (rotation is lossy resampling) and above 20° are
refused (`minAreaRect` bounds *all* dark pixels, so a figure can produce an angle
that has nothing to do with the baseline). Exposed corners are filled white — a
black wedge reads to Tesseract as content.

`tests/unit/test_deskew.py` rotates a known page by a known angle, in both
directions, and asserts it comes back. That is the test v2 never had.

---

## Local and cloud

Both engines, and both are real since M8.

The cloud engine is `tools/_cloud.py`'s shared flow plus a name and a validator
factory — the same two lines `compress` and `convert` are. **It is checked by
the same validators as the local engine**, so a server that returned a valid PDF
with an empty text layer fails exactly where a local run would, with the
destination untouched.

OCR is the case the Cloud Engine was built for: Tesseract, a pack per language
and Poppler is the project's most painful install, and
`architecture/overview.md` has used it as the justification since M0.

```bash
docmax cloud consent ocr          # once
docmax ocr scan.pdf -o out.pdf --engine cloud
```

Consent is per tool and enforced by `EngineRouter` before a strategy is built;
`offline = true` defeats `--engine cloud` regardless.

---

## Dependencies

| | |
|---|---|
| `tesseract` | recognition. `apt install tesseract-ocr` · `brew install tesseract` · `winget install UB-Mannheim.TesseractOCR` |
| `pdftoppm` | rasterisation, from Poppler. Shared with `to-images` |
| language packs | per language, e.g. `apt install tesseract-ocr-deu` |
| `DocmaxV3[ocr]` | OpenCV, **only for `--deskew`** |

`docmax ocr --no-deskew` needs no Python extra at all. `pytesseract` and
`pdf2image` are *not* used — ADR 0022 explains what that buys and what it costs.

`docmax doctor` reports on both binaries and has since M0.

---

## Testing

| Tier | What |
|---|---|
| pure logic | language and dpi parsing, the text-layer threshold, both validators |
| fake binaries | the whole pipeline — success, mixed documents, a failed page, a lost page, a blank layer, a timeout, a cancellation, atomicity — on a machine with no OCR software |
| deskew | the v2 regression, both directions, needs only OpenCV |
| `respx` | cloud round trip, and a cloud result failing the local validators |
| `needs_binary` + `golden` | one real Tesseract run, `eng` only |

`eng` only for the real test: `tesseract-ocr-deu` is installed on Linux in CI
and not on macOS or Windows.

**No golden output comparison.** Tesseract's text and its PDF bytes differ
between versions, so a golden file would be a maintenance liability rather than
a check — the `golden` mark on the one real test selects it into CI's
external-binary job, it does not compare bytes.
