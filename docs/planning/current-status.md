# Current status

**Last updated:** 2026-08-28 · **Branch:** `feat/m7-tui` · **Base:** `64ea3f4` (M6 complete)

This document describes what is true right now, not what is intended. If it
disagrees with the repository, the repository is right and this file is stale —
fix it.

---

## Where the project is

**Phases 0–9 are complete. Milestones M1 through M7 are done; the published
package is `DocmaxV3` 3.0.0a7, which predates them.**

| Phase | | |
|---|---|---|
| 0 — Repository baseline | complete | commit `4fc92f2` |
| 1 — Architecture mapping and docs | complete | this documentation system |
| 2 — Core contracts | complete | `models`, `protocols`, `atomic`, `cancellation` |
| 3 — Configuration | complete | `config`, `consent` |
| 4 — Tool registry | **complete** | arrived via `main`; `registry.py` is live |
| 5 — Engine router | **complete** | `core/router.py`; Core is now finished |
| 6 — `merge` as reference tool | **complete** | M1 closed; CLI wired to the router |
| 7 — M2 pypdf tool set | **complete** | split, rotate, pages, reorder, metadata, sanitize, get-info |
| 8 — M3 compress + binary doctor | **complete** | first external-binary engine |
| M4 — marking and encryption | **complete** | watermark, stamp, protect, unlock, permissions — no new phase; see the note below |
| M5 — conversion | **complete** | convert, to-images, from-images, `formats` — likewise no new phase |
| M6 — cloud, JSON, benchmarks | **complete** | cloud `compress`/`convert`, `--json`, `docmax cloud`, benchmark harness |
| 9 — TUI and visual pickers | **complete** | `docmax.tui`, `docmax.pickers`, the `crop` tool, M7 |

Phases 4 and much of 7/8 arrived by a route the phase plan did not anticipate:
`m1-foundations` was merged into `main` and released before the phase line
noticed. [ADR 0009](../adr/0009-main-is-the-base.md) records this.

**The phase numbers in this table are not the ones in
[phases.md](phases.md).** That file numbers the *engineering foundations* and
its Phases 7–9 are the cloud client, the HTTP server and the TUI; this table has
been reusing those numbers for the feature milestones that shipped on top of
them. The drift predates M4 and is not resolved here. M4 is listed without a
number rather than taking `9`, which phases.md has already spent on the TUI —
and it needs no phase of its own in any case, since phases.md says milestones
from M2 onward are increments over a finished foundation rather than new
foundations.

### Core as it stands

| Module | State |
|---|---|
| `branding.py` | done (M0) — unchanged by Phase 2 |
| `errors.py` | done (M0) — unchanged; already carried every error Phase 2 raises |
| `models.py` | done — `DocumentRef`, `OutputTarget`, `ToolResult`, `Engine` |
| `protocols.py` | done — `ProgressSink`, `NullProgress`, `EngineStrategy`, `Validator` |
| `atomic.py` | done — `atomic_write`, `atomic_path`, `atomic_dir` |
| `cancellation.py` | done — `CancellationToken`, `NEVER_CANCELLED` |
| `config.py` | done — precedence chain, validation, file locations |
| `consent.py` | done — `ConsentStore`, scoped to endpoint + terms version |
| `registry.py` | done — lazy discovery, entry points, `ToolSpec`/`Param` |
| `router.py` | done — resolution, consent gate, timing, error boundary |

### Tools

| Tool | State |
|---|---|
| `merge` | done — the reference implementation |
| `split` | done — and the first real consumer of `atomic_dir` |
| `rotate`, `pages`, `reorder`, `sanitize` | done |
| `metadata`, `get-info` | done — read-only paths write nothing |
| `compress` | done — Ghostscript, via `atomic_path`; needs the binary installed |
| `watermark` | done — vector text overlay, no new dependency and nothing rasterised |
| `stamp` | done — overlays another PDF's first page; the overlay is a second *input* |
| `protect` | done — AES-256 by default, which needs the `crypto` extra |
| `unlock` | done — needs a password that already opens the file; never guesses one |
| `permissions` | done — read-only, like `get-info` |
| `convert` | done — Pandoc, eight formats; **no PDF in or out**, see ADR 0011 |
| `to-images` | done — Poppler, one process per page; needs no imaging library |
| `from-images` | done — img2pdf + Pillow; the second multi-input tool after `merge` |
| `compress`, `convert` — cloud | done — M6; the only two with a cloud engine (ADR 0012) |
| `crop` | done — M7's one new tool; the headless half of the box picker, per ADR 0005 |
| `ocr` | **skeleton** — `run()` and both validators are `NotImplementedError`, M8 |

Shared between them: `tools/_pagespec.py` parses page selections once,
`tools/_pdf.py` opens and saves PDFs once, and `tools/_binaries.py` finds and
runs external programs once — with a timeout that cannot be forgotten and a
kill switch wired to the cancellation token. `doctor` reads the same
declaration, so the CLI and the tools cannot disagree about what is installed.

M4 added two more of the same kind: `tools/_position.py` owns the nine named
positions `watermark` and `stamp` share, and `tools/_permissions.py` owns the
permission vocabulary `protect` writes and `permissions` reads. Both exist for
`_pagespec`'s reason — a user who learns `bottom-right` or `copy` for one tool
must not find the other spells it differently. `_pdf.py` also grew
`open_encrypted_pdf`, the counterpart to `open_pdf` for the two tools whose
*subject* is a locked document.

M5 added the fourth: `tools/_formats.py` owns every format the three conversion
tools accept, and `docmax formats` renders it and holds no list of its own. That
command is the one `UnsupportedFormatError` has told users to run since M0 with
nothing behind it — see [ADR 0010](../adr/0010-format-vocabulary.md).

M6 added `tools/_cloud.py`, which is a different kind of shared module: not a
vocabulary but the whole cloud flow — upload, wait, fetch, validate, write
atomically — so a tool's `cloud.py` supplies only its name and its validators.
A cloud result goes through the *same* validators as a local one, which is what
makes the dual-engine promise one set of guarantees rather than two.

Beyond the tools: `cloud_client/` is implemented **and tested** — 46 `respx`
tests, which `cloud-api.md` claimed existed since M1 and which did not. `server/`
is complete: `RegistryRunner.start()` runs jobs in-process
([ADR 0016](../adr/0016-jobs-run-in-process.md)), and `GET /v1/capabilities`
reports what the deployment can actually run rather than what its build knows
about ([ADR 0018](../adr/0018-capabilities-mean-runnable.md)).

M7 added `tools/_box.py`, the fifth shared vocabulary: one rectangle syntax read
by `crop`, by the box picker, and by the CLI before either. And one function to
`tools/_pdf.py` — `page_geometry`, read-only — which is what lets a picker draw a
page to scale without learning pypdf's spelling of a media box.

The CLI exposes all eighteen working tools plus `doctor`, `formats`, `tui` and
the `cloud` group — every registered tool except `ocr`, which is still a
skeleton. Every command takes `--json`, including `tui`, which accepts it in
order to refuse in the envelope.

### Interfaces as they stand

| Interface | State |
|---|---|
| `cli` | complete — eighteen tools, four other commands, global `--json` |
| `tui` | done — M7. Generated from the registry; no per-tool code (ADR 0021) |
| `pickers` | done — M7. `crop` and `reorder`; parameters only (ADR 0005, 0019) |
| `server` | complete — routes, jobs, storage, auth, live tool execution |
| `mcp` | not built — M10 |

**M7 needed no change below the interface layer**, which was the phase's own
test of the core contracts. `ProgressSink` took a Textual widget unmodified,
`CancellationToken` took a keypress, and `ToolSpec`/`Param` generated eighteen
forms. `core/`, `registry.py` and `router.py` are untouched by this milestone.

`docmax.tui` and `docmax.pickers` arrived with their import-linter contracts in
the same change. `pickers` is deliberately *not* an interface — it never prints
and never exits — so it is in `LIBRARY_PACKAGES` and covered by the
no-direct-writes and no-sys-exit hygiene tests. That is what makes ADR 0005's
"a picker never touches the filesystem" a build failure rather than a promise.

---

## Verification

Network access was restored on 2026-08-15 and **the full toolchain now runs
locally**. Every check the project defines passes:

| Check | Result |
|---|---|
| `pytest -m "not golden and not needs_binary"` | **1693 passed, 2 skipped, 4 deselected** — 146s |
| `ruff check .` | **passed** |
| `ruff format --check .` | **passed** — 214 files already formatted |
| `mypy` (strict) | **passed** — no issues in 178 source files |
| `lint-imports` | **passed** — 5 contracts kept, 0 broken |

Both skips are intentional — the self-exemptions inside the hygiene suite:
`branding.py` may contain brand literals, and `atomic.py` may write directly.

Environment: Windows, CPython 3.14, `.venv` from `pip install -e ".[dev]"`,
plus `cryptography` (M4's `crypto` extra), `Pillow`/`img2pdf` (M5's `images`
extra), and `fastapi`/`uvicorn`/`respx` — M6 is the first milestone whose tests
need the **server** extra, because the only honest way to verify a cloud engine
is to run one against the reference server.

**`mypy` had to be reinstalled from its sdist to run here.** The wheel ships a
compiled `mypyc` extension, and this machine's Windows Application Control
policy blocks loading it — `pip install --no-binary mypy` produces the pure
Python build, which runs and passes. This is a property of the development
machine, not of the project; CI installs the wheel and is unaffected.

**Textual 8.2.8 is what M7 was verified against**, installed from the `tui`
extra. The extra's floor was raised from `>=0.60.0` to `>=1.0.0`, and that floor
is **not exercised** — nothing between 1.0 and 8.2 has been run. The 0.60 floor
was demonstrably wrong (`Select`'s "nothing chosen" sentinel has been renamed
since, and `push_screen_wait` did not exist), and `tui/app.py` avoids the APIs
known to have churned rather than pinning to one release.

**Still unverified:** the CI matrix — Linux and macOS, and Python 3.11–3.13.
Everything above is one platform and one interpreter, and 3.14 is not in the
project's supported matrix. The `golden` and `needs_binary` tests are also
unrun, since the external binaries are absent locally; CI requires them. M5
added two more of those: one real Pandoc conversion and one real Poppler render.
Everything else about both tools is covered by fake binaries that are real
subprocesses, following the pattern `compress` established at M3.

### What Phase 3 added

Phase 3 was written with the toolchain available throughout, and needed no
after-the-fact correction pass: `ruff check`, `mypy --strict` and `lint-imports`
were clean on the first run, and `ruff format` reformatted three files. That is
the contrast with Phase 2 below, and the argument for keeping the toolchain
installed.

### What the toolchain caught in Phase 2

Running the real tools after the fact found 17 `ruff` errors and 8 `mypy`
errors, **all in Phase 2 code, none previously visible** to the standalone
verifier. Worth recording because it calibrates how much a hand-rolled check is
worth:

- `PTH105` — `os.replace()` should be `Path.replace()`; the project enables the
  pathlib ruleset and the module had four call sites.
- `RUF100` — `noqa: SLF001` / `BLE001` directives for rules this project does
  not enable, which are themselves errors.
- `SIM105` / `S110` — `try/except/pass` in `cancel()`, now `contextlib.suppress`.
- `PT012` ×4, `PT017`, `N818` — pytest and naming conventions in the tests.
- `mypy` — comparing a `StrEnum` member to a string literal under
  `strict_equality`; asserting on the return of a `-> None` method; and one
  genuine narrowing trap, where `assert not token.is_cancelled` caused mypy to
  treat the rest of the test as unreachable.

All were fixed rather than suppressed.

---

## Blocked

Nothing. The earlier network constraint is resolved — `pip` reaches PyPI and the
full toolchain is installed and passing.

---

## Architecture violations and gaps

**None.** Every rule in
[dependencies.md](../architecture/dependencies.md) has a check behind it: five
import-linter contracts plus five hygiene tests, all green.

The list of unenforced rules that stood here through Phases 1–3 is empty. Those
checks arrived with the server, as the architecture said they must.

## Documentation status

| Document | State |
|---|---|
| `architecture/overview.md` | current — `EngineStrategy` signature corrected to match the code |
| `architecture/layers.md` | current — Core status updated |
| `architecture/dependencies.md` | current |
| `implementation/core.md` | current — new in Phase 2 |
| `implementation/config.md` | current — new in Phase 3 |
| `implementation/tui.md` | current — new at M7 |
| `adr/README.md` | current — indexes 0021 |
| `planning/*` | current. `reconciliation.md` is superseded by ADR 0009 and deletable |
| `cloud-api.md` | design-stage; data-handling now points at the terms constant |
| `README.md` | current |
| `CHANGELOG.md` | current through M7 |
| `development/*` | **missing** — no setup, testing or contributing guide |
| `api/*` | not needed until the HTTP layer exists |

---

## Decisions

[ADR 0006](../adr/0006-reference-server-location.md) resolved where the reference
server lives: `src/docmax/server/`, open, in this package. It supersedes the
`cloud_server/` clause of [ADR 0004](../adr/0004-open-core-boundary.md); the rest
of 0004 stands, and its text is unchanged apart from a pointer.

[ADR 0007](../adr/0007-m1-foundations-reconciliation.md) settled what happens to
the `m1-foundations` branch: preserved, never merged, ported component by
component.

[ADR 0008](../adr/0008-consent-record.md) settled the consent record ahead of
Phase 3's code: app-owned `consent.json` beside the user-owned `config.toml`,
scoped to `(tool, endpoint)` and a hand-bumped terms version, failing closed.

Two decisions remain owed an ADR before the code that needs them — the execution
model and observability. See [backlog.md](backlog.md#decisions-owed-an-adr).

---

## Related branch — `m1-foundations`

An earlier, broader attempt at the whole of M1, committed on a branch that forks
cleanly from `4fc92f2` and shares no commit with this line. It is pushed to
`origin/m1-foundations` and safe.

**It is a source branch, not a development line.** No merge, no deletion, no new
commits on it — see [ADR 0007](../adr/0007-m1-foundations-reconciliation.md) for
the decision and [reconciliation.md](reconciliation.md) for the evidence and the
outstanding component list.

What it holds, and who takes it:

| Component | Disposition | Phase |
|---|---|---|
| `core/registry.py` | port near as-is | 4 |
| `tools/merge/`, `tools/ocr/` | port layout; rewrite `run()` | 6 |
| `cloud_client/` | port, with `JobStatus` | 7 |
| `server/` + all enforcement config | port wholesale | 8 |
| its `core/{atomic,cancellation,models,protocols}.py` | **discard** | — |

The Core copies are discarded because they are the *pre-fix* version of what
Phase 2 now has: `ruff` reports in them the same ten errors Phase 2 already
fixed. Its `protocols.py` is additionally older — `run()` takes `progress` as
optional and has no `cancellation` at all.

**No architectural violations were found in it.** Layering is clean throughout:
`cloud_client` imports only `core` and `httpx`, `server` imports neither the
client nor the CLI, and no heavy dependency sits at module scope.

Two things worth knowing:

- **It already pays the entire enforcement debt.** Every item under
  [dependencies.md](../architecture/dependencies.md#not-yet-enforced) exists
  there — the `docmax.server` layer, the independence contract, the wheel
  exclusion and its test, the `server` extra. Phase 8 ports them rather than
  writing them.
- **It corroborates [ADR 0006](../adr/0006-reference-server-location.md).** That
  ADR reasoned the server belongs in-tree, open, and out of the wheel. This
  branch had independently built exactly that.

Stale bytecode directories left behind at `src/docmax/server/`, `tools/merge/`
and `tools/ocr/` have been removed.

---

## Next

**M8 — OCR, done properly.** `tools/ocr/` is a skeleton with a full `ToolSpec`
and a `run()` that raises. It is the operation v2 got most wrong, it has the
heaviest dependencies, and it is the tool the roadmap deliberately left last.
Shipping it also empties `tui/catalog.py`'s `UNIMPLEMENTED`, which a test will
insist on.

**Do not start it without direction.**

### What M7 left behind

**`redact` was not built, and neither was its picker.** [ADR
0005](../adr/0005-gui-pickers.md) names three pickers — `crop`, `redact`,
`reorder` — but the roadmap row says "crop and reorder", `redact` is on no
milestone, and it has no headless `--pattern` form for a picker to fill. Building
the picker first is precisely what that ADR forbids. The gap is now visible
rather than implied.

**The `crop` box uses a bottom-left origin**, which is the PDF coordinate
system's own and is not what someone counting down from the top of a viewer will
expect. It is documented in `--help`, in the picker page, and in
[implementation/tui.md](../implementation/tui.md); the picker converts, so only
users typing `--box` by hand meet it. Recorded because it is a contract decision
that would be expensive to reverse later.

**The reorder picker shows numbered cards, not thumbnails.** Thumbnails need
either a rasteriser (an external binary) or a vendored pdf.js, and
[ADR 0019](../adr/0019-picker-package-and-rendering.md) declined both. The
document is displayed alongside the list for reference. This is a real loss and
it is the price of the zero-dependency rule.

**The crop backdrop's alignment is browser-dependent.** The page is displayed
through the browser's own PDF viewer with `#view=Fit&toolbar=0`, which not every
browser honours identically. The returned coordinates are unaffected — they come
from the drawing surface, which is sized to the page's true aspect ratio — and
the numeric fields are always shown and editable.

**No performance numbers have been published.** The M6 harness exists and runs;
the development machine has neither Ghostscript nor Pandoc, so every row of the
one committed results file is `skipped` with a reason. Publishing a number needs
a machine with both installed.

**`convert` handles no PDF, in either direction, and that is the largest gap in
M5.** Pandoc has no PDF reader, and writing PDF needs a LaTeX distribution the
project does not install; [ADR 0011](../adr/0011-convert-is-pandoc-only.md)
records the decision and its cost. `docmax convert report.pdf --to docx` is the
command most users will try first and it is refused — with a message naming the
limitation and pointing at `to-images`, but refused. M6's cloud engine is the
architecture's own answer to it.

**`architecture/overview.md` names "Pandoc + a LaTeX distribution" for
`convert`.** That describes the M6 cloud engine, which is what the table's
column heading says. It is accurate about M6 and ahead of M5.

**A fourth instance of the `ToolSpec` seam arrived at M7, and was reported
rather than patched.** `ToolSpec` cannot say "declared but not implemented", so a
registry-driven tool list offers `ocr` — whose `run()` raises — and choosing it
would produce an `InternalError` wrapping a `NotImplementedError`. The TUI names
that one exception in `tui/catalog.py`, with two tests holding it, and the clean
fix (an `implemented` flag on `ToolSpec`) is a Core change deliberately not made.
[phases.md](phases.md) says to treat exactly this as a finding, and it belongs
with the other three below rather than being decided alone.

**Four seams are now owed one ADR.** `ToolSpec` cannot say "this tool produces
no output" (`get-info`, a bare `metadata`, `permissions`), cannot say "my output
extension depends on a parameter" (which is why `convert` requires `-o`), and
cannot carry configuration to a strategy — so a cloud strategy resolves its own
through `core.config.load()` rather than receiving the router's
([ADR 0013](../adr/0013-cloud-config-comes-from-the-resolved-config.md)) — and
cannot say "declared but not implemented" (M7, above). All four are the same
shape: a tool wanting something from `ToolSpec` that it does not carry. They
should be decided together.

The third has a named trigger: **adding an `--endpoint` or `--api-key` flag
makes it wrong**, because a runtime override cannot reach a strategy.

**`permissions` reads and does not write, and that was a judgement call.**
Nothing in the repository documented what the tool should do. `metadata`'s
dual read/write shape was the obvious precedent, but writing a permission bit
means encrypting the document — algorithm, user password, owner password — which
is the whole of `protect`. Rather than have two tools implement encryption,
`protect --allow` sets permissions and `permissions` reports them. Recorded here
because it is a contract decision made in the absence of one, and it should be
confirmed or overturned deliberately.

**Ghostscript is not installed on the development machine**, so the two
`needs_binary` compress tests are skipped locally and required in CI. Everything
else about compress is covered by a stand-in program that is a real subprocess.

Each phase is scoped explicitly, and none should be started without direction.
