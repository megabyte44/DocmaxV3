# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [semantic versioning](https://semver.org/).

## [Unreleased] — 3.0.0

DocMax 3 is a clean-room rewrite. v2 was a working, published tool; it was also
structurally unable to grow — no tests, no atomic writes, no typed errors, and
a single-engine design with hardcoded dispatch. v3 keeps the command surface
and rebuilds everything underneath it.

### Breaking

These are behaviour changes a v2 user can actually hit. See
[docs/migrating-from-v2.md](docs/migrating-from-v2.md).

- **A bare `docmax` may now open the TUI** instead of printing help — but only
  at an interactive terminal, with `textual` installed, and without `--json`.
  Piped, redirected, in CI, or on a machine without the `tui` extra, it prints
  help and exits 0 exactly as before, so no script changes behaviour.
- **`docmax reorder` accepts `--interactive` in place of `--order`.** `--order`
  is still required in every non-interactive invocation; what was a missing-
  option error from the argument parser is now the same refusal naming both
  ways to supply it.
- **Existing files are no longer silently overwritten.** Writing to a path that
  exists now requires `--force`. In v2, re-running any command destroyed the
  previous run's output without warning.
- **An input can no longer be used as the output.** `convert x.md --to md` and
  `merge a.pdf b.pdf -o a.pdf` now raise `output.in_place_overwrite` instead of
  destroying the source file.
- **Python 3.11 is the minimum** (v2 supported 3.9+). See
  [ADR 0001](docs/adr/0001-python-311.md).
- **Errors are typed and no longer exit the process from library code.** Anyone
  importing `docmax.*` as a library gets exceptions instead of `sys.exit(1)`.

### Added

- **A Textual TUI.** `docmax tui`, or a bare `docmax` at an interactive
  terminal. A second driver of the same core, not a second implementation:
  every run goes through `EngineRouter`, and there is no routing, consent rule,
  validation or atomic write anywhere in the interface.
  - **Generated from the registry.** One screen serves all eighteen tools,
    built from `ToolSpec` and `Param` — the job `Param`'s docstring has
    described since M0. There is no per-tool code in the TUI and a test
    asserts it, because a hardcoded dispatch chain over tool names is exactly
    how v2's interactive menu died. See
    [ADR 0021](docs/adr/0021-the-tui-is-generated-from-the-registry.md).
  - Runs on a worker thread so `ctrl+c` can cancel; the atomic writers then
    leave the destination untouched, exactly as on the command line.
  - Consent is the modal `errors.py` has specified since M0 and which nothing
    implemented; errors are a message and a remedy, never a traceback.
  - Optional: `pip install "DocmaxV3[tui]"`. Without it, `docmax tui` reports
    the install line rather than an `ImportError`, and a bare `docmax` prints
    help exactly as before.
- **Visual pickers for `crop` and `reorder`.** `--interactive` opens a page in
  your browser, you drag a box or rearrange the pages, and the value fills
  `--box` or `--order`. Everything after that is identical to typing it.
  - **A picker returns parameters, never results** — the rule
    [ADR 0005](docs/adr/0005-gui-pickers.md) set at M0. Nothing in
    `docmax/pickers/` writes a file, exits a process, or imports an engine, and
    all three are enforced by the hygiene suite rather than by review.
  - Zero new dependencies **and zero vendored assets**: `http.server`,
    `webbrowser`, and the browser's own PDF viewer instead of a bundled copy of
    pdf.js. [ADR 0019](docs/adr/0019-picker-package-and-rendering.md) records
    that departure from ADR 0005's implementation note and what it costs.
  - Over SSH the picker prints a URL, and the flag form works regardless.
- **`crop`.** Trim every page to a rectangle, in points from the bottom-left.
  New at M7 because ADR 0005 requires a picker's headless form to ship first
  and this one never had: `--box` is the tested path, `--interactive` only
  fills it in. Sets `/CropBox` as well as `/MediaBox`, since a viewer prefers
  the former and writing only the latter looks cropped in some readers and not
  others. A page the box does not fit is left alone and named in the result.
- **`docmax.tui` and `docmax.pickers` joined the layering contracts** in the
  same change as the code, as the architecture requires — five contracts still
  kept, with one narrow, documented and separately tested exception for the
  entry point ([ADR 0020](docs/adr/0020-tui-entry-point.md)).
- **Cloud engines for `compress` and `convert`.** Two, not the five the
  architecture docs name: `ocr` is M8's, and `pdfa` and `remove-bg` do not
  exist. A cloud engine for a tool with no local engine is exactly what the
  roadmap's ordering forbids — see
  [ADR 0012](docs/adr/0012-cloud-engines-are-compress-and-convert.md).
  - The flow — upload, wait, fetch, validate, write atomically — lives once in
    `tools/_cloud.py`; a tool's `cloud.py` supplies its name and its validators
    and nothing else.
  - **A cloud result is checked by the tool's own validators.** A cloud
    `compress` that lost a page fails exactly where a local one would, and the
    destination is untouched either way.
  - **No automatic fallback to local.** The router picks one engine before any
    work starts; a cloud failure is reported as one. Silently re-running would
    double the work on a document you have already paid to upload.
  - `docmax cloud login | logout | status | consent | revoke`, and the consent
    prompt `errors.py` has described since M0 and which nothing implemented —
    a user who met `engine.consent_required` was previously told to agree with
    no way to do it.
- **`--json` on every command.** One object on stdout, diagnostics on stderr,
  exit codes unchanged. The error envelope is the one the Cloud API already
  returns, so a script parsing a DocMax failure writes one parser for both. See
  [docs/implementation/json.md](docs/implementation/json.md) and
  [ADR 0017](docs/adr/0017-json-output-contract.md).
- **A benchmark harness** — `python -m benchmarks`. Two tools, four generated
  fixtures, one warmup and five timed runs reported as median and minimum, with
  the machine recorded alongside. **No numbers are published**: the development
  machine has neither Ghostscript nor Pandoc, so every row of the committed
  results file says so. See
  [benchmarks/METHODOLOGY.md](benchmarks/METHODOLOGY.md).
- **The reference server runs tools.** `RegistryRunner.start()` was a
  `NotImplementedError`; jobs now run in the request that submits them, with the
  staged input deleted on failure as well as on success —
  [ADR 0016](docs/adr/0016-jobs-run-in-process.md), which also closes the
  execution-model decision the backlog has owed since Phase 1.

### Fixed in M6

- **`[cloud] endpoint` was documented, parsed, and silently ignored.** The
  registry constructs a strategy with no arguments, so the only cloud strategy
  fell back to reading the environment — and a user who pointed DocMax at a
  self-hosted deployment in `config.toml` had their documents sent to the
  default endpoint instead. For a feature whose premise is "your documents go
  where you say", that was the worst possible direction to fail in.
  [ADR 0013](docs/adr/0013-cloud-config-comes-from-the-resolved-config.md).
- **Ctrl-C could not interrupt a cloud job.** `CloudClient.wait()` slept in
  uninterruptible blocks and had no way to receive a cancellation token, so an
  interrupt went unnoticed for up to the server's whole polling interval. The
  same class of defect v2 shipped with subprocesses, through a different door.
  [ADR 0015](docs/adr/0015-cancellation-crosses-the-network.md).
- **The client's read timeout was shorter than its own job timeout** — 120s
  against 900s — so a long synchronous job was killed from the client side
  while the server was still succeeding.
- **`GET /v1/capabilities` advertised a tool it could not run.** It answered
  from the build rather than from the deployment, so an endpoint with nothing
  installed listed `ocr`. It now asks each tool whether it is available on that
  machine. [ADR 0018](docs/adr/0018-capabilities-mean-runnable.md).
- **`cloud-api.md` claimed `respx`-based client tests existed.** They did not:
  the cloud client shipped untested through five milestones. It now has 46.
- **The M5 conversion tools** — `convert`, `to-images` and `from-images`, plus
  the `docmax formats` command.
  - `convert` runs Pandoc over eight formats: Markdown, HTML, Word,
    OpenDocument, reStructuredText, LaTeX source, EPUB and plain text.
    **PDF is not supported in either direction** — Pandoc has no PDF reader, and
    writing PDF needs a LaTeX distribution DocMax does not install. Both
    refusals name the limitation and point at `to-images` rather than reporting
    an unknown format. See
    [ADR 0011](docs/adr/0011-convert-is-pandoc-only.md).
  - `to-images` renders pages with Poppler's `pdftoppm`, one process per page so
    that page selections need not be contiguous and progress advances per page.
    It needs **no Python imaging library**: pdftoppm writes PNG, JPEG and TIFF
    itself, and loading each page into Pillow only to write it out again would
    re-encode it for nothing. The output is a directory, staged and swapped in
    as a unit.
  - `from-images` assembles images into a PDF, one page per image, in argument
    order and at each image's own size. JPEGs are embedded without being
    re-encoded. It is the second multi-input tool after `merge`, so `-o`
    pointing at one of the images is refused rather than destroying it.
  - **`docmax formats`** finally exists. `UnsupportedFormatError` has told users
    to run it since M0 with nothing behind it — the error was nearly unreachable
    while every tool accepted only PDF, and M5 made it both reachable and wrong.
    It renders `tools/_formats.py` and holds no list of its own, so `--to`'s
    help, the validation that refuses a bad value, and the table are provably
    the same declaration. Formats that cannot be used are shown *with the
    reason*, because "unknown format: pdf" teaches nothing. See
    [ADR 0010](docs/adr/0010-format-vocabulary.md).

  Every image `to-images` writes is checked for a real header of its format
  before the directory replaces anything — the exact failure v2's
  `extract_images` shipped, and the one the validator mechanism was written for.
- **The M4 tool set** — `watermark`, `stamp`, `protect`, `unlock` and
  `permissions`, all pure pypdf and all reached through the same router.
  - `watermark` draws vector text over the page in Helvetica, one of the
    fourteen faces every PDF reader has built in — so nothing is embedded,
    nothing is rasterised, and no new dependency was needed. Text the standard
    font cannot draw is refused rather than silently rendered as the wrong
    glyphs.
  - `stamp` overlays another PDF's first page. The overlay is passed as a second
    *input* rather than as a path parameter, so the existing "an input can never
    be the output" check covers it and `stamp a.pdf --stamp logo.pdf -o logo.pdf`
    is refused instead of consuming the logo while reading it.
  - `protect` encrypts with **AES-256 by default**, which needs the new `crypto`
    extra. The weaker RC4 algorithms still work with no extra install but have
    to be named explicitly: a tool called `protect` should not quietly hand you
    broken encryption. Its output is checked for *being encrypted* before it
    replaces anything, because a readable unencrypted PDF is the one failure
    here that looks exactly like success.
  - `unlock` removes a password you already have. It does not recover, guess or
    break one. Because PDF permissions live inside the encryption dictionary,
    an unlocked copy allows everything — stated rather than left to be found.
  - `permissions` reports what a document allows, and says plainly that those
    bits are advisory: a reader that ignores them is not breaking anything.
    Setting them is `protect --allow`, so encryption has one implementation
    rather than two.

  Shared vocabularies keep the five consistent with each other and with what came
  before: `_position.py` owns the nine named positions `watermark` and `stamp`
  share, and `_permissions.py` owns the eight permission names `protect` writes
  and `permissions` reads.
- **A `crypto` extra** — `pip install "DocmaxV3[crypto]"`, which is what pypdf
  needs to read or write anything stronger than RC4. Absent, `protect` names the
  install line instead of failing obscurely or downgrading in silence.
- **`compress`** — shrinks a PDF with Ghostscript, and the first engine that is
  another program rather than a Python library. Output goes through
  `atomic_path`, so a Ghostscript that fails, hangs, or exits zero having
  written nothing leaves the destination untouched; the page count is verified
  before the result replaces anything, because a smaller file with fewer pages
  is not a compressed document.
- **External-binary support in `doctor`** — one declaration now serves both the
  report and the engines, where the list previously lived in the CLI layer where
  no tool could reach it. `doctor` prints the install command for the running
  platform, and Ghostscript's Windows spellings (`gswin64c`, `gswin32c`) are
  recognised, so it is no longer reported missing on machines where it is
  installed. Every external call takes a timeout from the cancellation token and
  is killed on Ctrl-C rather than waited out.
- **The M2 tool set** — `split`, `rotate`, `pages`, `reorder`, `metadata`,
  `sanitize` and `get-info`, all pure pypdf and all reached through the same
  router. `split` is the first tool to produce many outputs, so it is the first
  real exercise of the guarantee that a cancelled multi-file run leaves no
  partial directory. Page selections (`1-3,7`, `4-`, `-2`) are parsed in one
  place, so the syntax a user learns for one tool is the syntax for all of them.
  `sanitize` documents exactly what it removes and explicitly what it does not.
- **The engine router** — the single path from "run this tool" to "here is the
  result", which every interface calls and nothing else. It owns engine
  resolution, the consent gate, cancellation and progress plumbing, timing, dry
  runs, and the boundary where an untyped exception becomes an `InternalError`
  rather than a traceback. `offline` beats an explicit `--engine cloud` and is
  checked before consent, so a policy never surfaces as a prompt; and every
  route to the cloud — including the automatic fallback when a local dependency
  is missing — passes one consent checkpoint.
- **Configuration and consent** — one precedence chain (defaults → file →
  environment → runtime override), read in exactly one place, with validation at
  load rather than at use: unknown keys are refused rather than silently
  ignored, and TLS is required for any endpoint that is not localhost. `offline`
  is one-way, so an explicit `--engine cloud` cannot defeat it. Consent to
  upload is recorded per tool, scoped to the endpoint it was granted for, and
  fails closed on any record it cannot read. See
  [ADR 0008](docs/adr/0008-consent-record.md).
- Dual-engine architecture — every tool can run locally or via a cloud endpoint
  behind one interface, chosen per tool.
- The three working halves of the package, in skeleton: `tools/` (one
  self-registering package per operation, with `merge` and `ocr` as the
  local-only and dual-engine references), `cloud_client/` (the wire contract,
  idempotency, server-controlled polling, retries only where retrying helps),
  and `server/` (the reference implementation of the same contract, which runs
  the same engines rather than reimplementing them).
- A lazy tool registry: discovery walks `tools/*/tool.py` and reads
  `importlib.metadata` entry points, so adding tool #51 edits no central file
  and listing tools imports no tool.
- `DocumentRef`, `OutputTarget`, `ToolResult`, and the `EngineStrategy` /
  `ProgressSink` / `Validator` protocols the layers meet at.
- `OutputTarget`, which makes in-place overwrite unrepresentable rather than
  merely discouraged.
- Atomic writes for every operation: temp file → validate → `os.replace()`.
- A typed error hierarchy where every error carries a stable code and an
  actionable remedy.
- Five structural hygiene tests, enforced in CI: no process exits in library
  code, no writes outside the atomic helpers, no heavy imports at module scope,
  no brand literals outside `branding.py`, and no shipped module importing the
  server, which is excluded from the wheel.
- CI across Linux, macOS, and Windows × Python 3.11, 3.12, 3.13, plus an
  `open-core` job that runs the suite with the licence-gated half deleted.
- Architecture documentation and ADRs 0001–0009, with an indexed decision log,
  per-layer and dependency references, and a planning system that separates the
  product roadmap from the engineering phases underneath it.

### Changed

- **`EngineStrategy.run()` now requires `progress` and `cancellation`.** Both
  were previously optional or absent, which meant a tool could not be cancelled
  by its caller at all. `NullProgress` and `NEVER_CANCELLED` exist so the
  arguments can be required without burdening callers, and requiring them
  removes the `if progress is not None` branch from every engine rather than
  leaving a path that only runs in tests. This changes a contract published in
  `3.0.0a7`; no tool implements `run()` yet, so nothing observable changed.

### Architecture

- [ADR 0009](docs/adr/0009-main-is-the-base.md) — `main` is the authoritative
  base, superseding ADR 0007. That ADR recorded that `m1-foundations` would
  never be merged; it had in fact already been merged and released, and the
  phase line had been developing against a stale remote without noticing.

### Fixed

Correctness bugs carried by v2, fixed by the rewrite rather than patched:

- `extract_images` wrote decoded FlateDecode and CCITTFax streams with `.png`
  and `.tiff` extensions but no container headers, producing files nothing
  could open.
- `deskew` passed `(y, x)` points to `cv2.minAreaRect`, which expects `(x, y)`,
  and applied an angle correction written for a pre-4.5 OpenCV convention that
  never fires on current versions.
- The settings screen wrote config keys that nothing ever read — "settings
  saved" was untrue.
- One menu entry imported a function that did not exist, raising `ImportError`
  and terminating the interactive session.
- `extract_tables` ignored the requested format and always produced CSV.
- No subprocess had a timeout; a hung `xelatex` hung DocMax indefinitely.
- Temp files and directories from OCR, watermarking, and PDF rasterisation were
  never cleaned up, and OCR wrote a `_preprocessed.png` beside every source file
  it touched — which also caused folder-watch mode to feed on its own output.

## [2.0.4] and earlier

See the [v2 repository](https://github.com/megabyte44/DFORGE).
