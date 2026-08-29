# Current status

**Last updated:** 2026-08-29 · **Branch:** `feat/m10-mcp` · **Base:** `498cc4e` (M9 complete)

This document describes what is true right now, not what is intended. If it
disagrees with the repository, the repository is right and this file is stale —
fix it.

---

## Where the project is

**Phases 0–10 are complete. Milestones M1 through M10 are done — the whole
roadmap, with one row of M9 deliberately not delivered, below; the published
package is `DocmaxV3` 3.0.0a7, which predates all of them.**

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
| M8 — OCR | **complete** | `ocr` local and cloud, `tools/_deskew.py`, `tools/_dpi.py` — no phase of its own, as for M4/M5 |
| M9 — pipelines, batch, watch | **complete** | `docmax.runners`, three new commands, ADRs 0023–0026 — likewise no phase of its own. `--resume` deferred |
| 10 — MCP server | **complete** | `docmax.mcp`, `docmax mcp`, the `mcp` extra, ADRs 0027–0030. M10 |

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
| `ocr` | done — M8. The last skeleton; Tesseract + Poppler, both engines |

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

M8 added the sixth shared vocabulary, `tools/_dpi.py` — one set of resolution
bounds read by `ocr` and `to-images`, so `--dpi` cannot mean two things — and
`tools/_deskew.py`, which is not a vocabulary but a pure function isolated so
that v2's deskew defect could finally be tested.

M7 added `tools/_box.py`, the fifth shared vocabulary: one rectangle syntax read
by `crop`, by the box picker, and by the CLI before either. And one function to
`tools/_pdf.py` — `page_geometry`, read-only — which is what lets a picker draw a
page to scale without learning pypdf's spelling of a media box.

The CLI exposes **all nineteen tools** plus seven other commands — `doctor`,
`formats`, `tui`, M9's `pipeline`, `batch` and `watch`, and M10's `mcp` — and
the `cloud` group. There is no longer any registered tool it does not expose.
Every command takes `--json`, including `tui` and `mcp`, which accept it in
order to refuse in the envelope: both own stdout for something else. The check
that says so is parametrised over the registered commands, so each new command
inherited it rather than being added to a list.

M9 added no shared tool vocabulary, because it added no tool. What it added is
`docmax.runners`: `pipeline.py` (the TOML format, validation and the staged
chain), `batch.py` (naming, the two contamination checks, per-item isolation)
and `watch.py` (polling, settling, the digest key and the containment rule).
`_progress.py` is the one piece of glue — a sink that prefixes an item label
onto whatever description the tool sets, so "which document" and "which stage"
are reportable without changing `ProgressSink`.

### Interfaces as they stand

| Interface | State |
|---|---|
| `cli` | complete — nineteen tools, seven other commands, the `cloud` group, global `--json` |
| `tui` | done — M7. Generated from the registry; no per-tool code (ADR 0021) |
| `pickers` | done — M7. `crop` and `reorder`; parameters only (ADR 0005, 0019) |
| `runners` | done — M9. `pipeline`, `batch`, `watch`; library code, imports only `core` (ADR 0023) |
| `server` | complete — routes, jobs, storage, auth, live tool execution |
| `mcp` | done — M10. stdio JSON-RPC, generated from the registry, inside a root policy (ADRs 0027–0030) |

**M7 needed no change below the interface layer**, which was the phase's own
test of the core contracts. `ProgressSink` took a Textual widget unmodified,
`CancellationToken` took a keypress, and `ToolSpec`/`Param` generated eighteen
forms. `core/`, `registry.py` and `router.py` are untouched by this milestone.

**M9 needed no change either, and it had more reason to want one.** `core/`,
`registry.py` and `router.py` are untouched by this milestone too. `EngineRouter.run`
took a composed caller unmodified; `OutputTarget.resolve` guarded every staged
intermediate as well as every final destination; `CancellationToken` stopped a
batch between documents and a watch between ticks with no new mechanism; and the
atomic writers made "a failed three-stage pipeline leaves nothing behind" fall
out rather than need building. `core-is-standalone` now lists `docmax.runners`
among the modules `core` may not import, so that claim is checked rather than
asserted.

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
| `pytest -m "not golden and not needs_binary"` | **2143 passed, 4 skipped, 5 deselected** — 220s |
| `ruff check .` | **passed** |
| `ruff format --check .` | **passed** — 255 files already formatted |
| `mypy` (strict) | **passed** — no issues in 203 source files |
| `lint-imports` | **passed** — 5 contracts kept, 0 broken |

The fourth skip is M10's, and it is environmental rather than intentional: the
MCP symlink-escape test needs a privilege Windows does not grant by default, so
it has never run on this machine and runs for the first time in CI.

**M9 added 158 of those and M10 added 203.** M9's were 130 in the five
`test_m9_*.py` files (43 pipeline, 27 batch, 29 watch, 17 CLI/JSON, 14
architectural) plus 28 inherited; M10's were 186 in the five `test_m10_*.py`
files (34 protocol, 110 schema, 22 policy, 9 cancellation, 11 packaging) plus 17
inherited. Nothing inherited had to be written: the hygiene suites are
parametrised over `LIBRARY_PACKAGES` — which now includes `runners` and `mcp` —
and `test_cli_json.py` is parametrised over the registered commands, so each new
command was covered by the `--json` contract the moment it was registered.

**Two existing tests failed during M9 and both were right to.**
`test_brand_literals_only_in_branding` caught a hardcoded `"docmax-pipeline-"`
temp prefix in `runners/pipeline.py`; it now derives from `CLI_NAME`, as
`core/atomic.py` already did. `test_the_tui_offers_exactly_what_the_cli_exposes`
caught the three new commands as CLI surface the TUI does not offer — correct,
since none of them is a registered tool, so the allowed non-tool set grew from
three names to six. Neither test was weakened: `offered <= exposed` still holds
strictly, and the brand rule was obeyed rather than exempted.

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

**No OCR binary is installed on this machine.** Neither Tesseract nor Poppler,
as for Ghostscript and Pandoc. The whole OCR contract is therefore verified
against *fake* binaries that are real subprocesses — success, mixed documents, a
failed page, a lost page, a blank text layer, a timeout, a cancellation,
atomicity — and the one real-recognition test is `needs_binary` and skipped
here. OpenCV **is** installed locally, so the deskew regression genuinely runs.

**Two `needs_binary` tests fail here rather than skipping, and they are not
M8's.** `test_convert_really_converts_markdown_to_html` and
`test_to_images_really_renders_a_page` carry `@needs_pandoc` / `@needs_poppler`
but no `skipif`, unlike the `compress` tests M3 wrote — so on a machine without
the binary they fail instead of skipping. Confirmed pre-existing by stashing
every M8 change and re-running. Left alone deliberately: fixing an M5 test is
not M8's work. The new OCR test follows M3's pattern, marker **and** `skipif`,
so it skips cleanly.

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
| `implementation/ocr.md` | current — new at M8 |
| `implementation/runners.md` | current — new at M9 |
| `implementation/mcp.md` | current — new at M10 |
| `adr/README.md` | current — indexes 0030 |
| `planning/*` | current. `reconciliation.md` is superseded by ADR 0009 and deletable |
| `cloud-api.md` | design-stage; data-handling now points at the terms constant |
| `README.md` | current |
| `CHANGELOG.md` | current through M10 |
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

**The roadmap is complete.** M0–M10 are all delivered. What remains is
[backlog.md](backlog.md), and three items there now carry more weight than they
did:

- **The three `ToolSpec` seams**, which four interfaces have now met and none has
  closed. M10 declined a fourth (`input_suffixes`) on the same grounds M7
  declined `implemented`. This is the most pressed-upon decision in the project.
- **Phase 11 — contributor experience.** `docs/development/` is still missing,
  and the project now has four interfaces to explain rather than one.
- **The CI matrix**, unverified since Phase 2 and now covering five packages more
  than it did.

**Do not start anything without direction.**

### What M10 left behind

**No progress reaches an MCP client.** A long call is silent until it returns.
The protocol has a progress notification; using it needs the request's progress
token plumbed through and a `ProgressSink` emitting async notifications from a
worker thread — the one place this interface would have to think about
thread-safety across the async boundary. Deliberately additive, per
[ADR 0030](../adr/0030-mcp-cancellation-maps-onto-the-cancellation-token.md).

**The schema cannot say what a tool reads.** `inputs` is "a path", not "a PDF",
because `ToolSpec` carries nothing describing input formats. This would have been
a **fourth** seam and M10 refused to open it —
[ADR 0028](../adr/0028-the-mcp-tool-surface-is-the-registry.md). The cost is one
wasted round trip when a client hands `ocr` a spreadsheet.

**The M9 runners are not exposed, and that is a recorded contradiction.**
`docs/plans/05` proposed an MCP `run_pipeline` and ADR 0023 anticipated one, but
pipelines, batch and watch are not registered tools — offering them needs a
hand-written list, which ADR 0021 and `CLAUDE.md` rule 1 forbid. The registry
rule won; a test holds the absence.

**A TOCTOU window exists in the root check.** A path validated and then replaced
with a symlink before the tool opens it would escape. Closing it needs
`O_NOFOLLOW`-style handling inside every engine — Core, and nineteen tools.
Small in the local-agent threat model, and named rather than fixed.

**Roots are enforced at the interface, not in `OutputTarget`.** That was the
closest call in M10: pushing it down would make it unbypassable, and would also
put an agent-shaped policy into the type every CLI call uses, where a user
naming a path has already authorised it. If a second non-human caller ever
appears, the decision is revisited rather than copied —
[ADR 0029](../adr/0029-the-mcp-policy-boundary.md).

**The SDK floor is verified at exactly one point.** `mcp>=2.1,<3`, exercised
against 2.1.1 on Windows / CPython 3.14. The `tui` extra has the same weakness
with Textual, recorded below.

**`docs/plans/` and `CLAUDE.md` exist only on `main`.** The audit that opened M10
found its specification there — `docs/plans/05-mcp-pull-forward.md` — and that
file is on no branch in this line. `main` has diverged since `4a5d74a` (M3) and
also carries `3ea4d3e fix(core): compare output against inputs by identity, not
by path string`, which touches `models.py`. Reconciling that divergence is
unscheduled and is now the oldest untended thing in the repository.

### What M9 left behind

**`--resume` does not exist**, and the roadmap row says "resumable batch". It
was deferred rather than invented: a journal is a persistent, app-owned file
format that deserves ADR 0008's treatment — a decided location, a schema
version, and defined behaviour on a corrupt record — rather than a shape
improvised alongside three other features. `core/errors.py`'s `CancelledError`
docstring promises it and is currently ahead of the code, which is the one place
in the repository where that is true on purpose. The watcher's processed-file
set is in memory for the same reason and does not survive a restart. See
[roadmap.md](roadmap.md#what-m9-did-not-deliver).

**Batch is serial**, and a 200-file OCR batch is therefore 200 sequential OCR
runs. That is the honest cost of not building concurrency, and
[ADR 0025](../adr/0025-batch-mirrors-names-into-an-output-directory.md) names
the three contracts that would have to change first — the process-boundary work
the backlog has recorded since M6. The open question that entry carried turned
out not to need answering: serial execution keeps `CancellationToken`,
`ProgressSink` and `ConsoleProgress` true exactly as written.

**The watcher polls, and watches one directory.** No `watchdog`, no recursion.
Latency is up to one interval plus one settle tick — about two seconds at the
default — which is the price of not taking a dependency whose event semantics
differ on all three platforms this project has never verified on any of them.

**Watching `inbox/` and writing to `inbox/done/` is refused**, which is a real
workflow the containment rule blocks. Accepted deliberately: excluding a subtree
from the scan is the same feature as recursive watching, and reintroduces the
loop the rule exists to prevent.

**A `ConsentRequiredError` inside a pipeline or batch is not a prompt.** The
single-document path in `cli/execution.py` asks; a composed run reports the
error and its remedy instead. Stopping to ask inside a batch of two hundred is
the interaction ADR 0008 warns about, but it does mean the two paths differ.

**The two frozensets in `runners/pipeline.py` are the `ToolSpec` seam again.**
`NOT_A_MIDDLE_STAGE` is its fifth appearance and `SUFFIX_FROM_PARAMS` is
literally the third of the three seams named below — the one about an output
extension that depends on a parameter. Neither can be derived from the registry,
so both are hand-maintained lists held by tests, exactly as `tui/catalog.py` was
at M7. **A new tool that produces a directory will not be added to the set by
any test**, and will fail at run time on the next stage instead. This is now the
strongest argument yet that the three seams should be decided together.

### What M8 left behind

**There is no `--force-ocr`.** A page that already carries text is copied
through, and a user who genuinely wants it re-recognised has no flag for it.
`ToolSpec` promises three parameters and M8 did not invent a fourth; ADR 0022
records the reasoning, and the flag is additive if it is ever wanted.

**A recognised page comes back as an image at `--dpi`.** That is inherent to
Tesseract's PDF output and to OCR of a scan generally, but it means running
`ocr` over a document that is mostly text and slightly scanned rasterises the
scanned pages. The skip rule is what keeps it from touching the text pages.

**`docmax setup` still does not exist.** M8 removed the dangling reference to
`setup --ocr` from the OCR install hint, but the backlog item is untouched and
OCR is the tool that would benefit most from it.

**No benchmark numbers, still.** OCR is the most obvious candidate — it is by
far the slowest operation — and this machine has no Tesseract.

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

**The fourth `ToolSpec` seam closed itself at M8, as predicted.** `ocr` shipped,
`tui/catalog.py`'s `UNIMPLEMENTED` is empty, and a test asserts it stays empty.
No Core change was needed — which is the outcome ADR 0021 argued for when it
deferred the decision. **Three seams remain**, and they are the original three.

**ADR 0018's named residual is closed.** That ADR recorded that on a machine
with Tesseract installed, `ocr` would be advertised by `/v1/capabilities` and
still fail, *"resolved when M8 implements the method"*. It is.

**The M7 finding, for the record.** `ToolSpec` cannot say "declared but not implemented", so a
registry-driven tool list offers `ocr` — whose `run()` raises — and choosing it
would produce an `InternalError` wrapping a `NotImplementedError`. The TUI names
that one exception in `tui/catalog.py`, with two tests holding it, and the clean
fix (an `implemented` flag on `ToolSpec`) is a Core change deliberately not made.
[phases.md](phases.md) says to treat exactly this as a finding, and it belongs
with the other three below rather than being decided alone.

**Three seams are still owed one ADR.** `ToolSpec` cannot say "this tool produces
no output" (`get-info`, a bare `metadata`, `permissions`), cannot say "my output
extension depends on a parameter" (which is why `convert` requires `-o`), and
cannot carry configuration to a strategy — so a cloud strategy resolves its own
through `core.config.load()` rather than receiving the router's
([ADR 0013](../adr/0013-cloud-config-comes-from-the-resolved-config.md)) — and
— and the fourth, "declared but not implemented", is gone. All three are the
same shape: a tool wanting something from `ToolSpec` that it does not carry.
They should be decided together.

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
