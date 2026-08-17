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
