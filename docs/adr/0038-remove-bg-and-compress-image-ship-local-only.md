# ADR 0038 — `remove-bg` and `compress-image` ship local-only

**Status:** Accepted · 2026-09-02

## Context

[ADR 0012](0012-cloud-engines-are-compress-and-convert.md) established an
ordering rule: **a cloud engine for a tool with no local engine is exactly
what the roadmap forbids.** That ADR decided M6 would ship cloud engines for
`compress` and `convert` only, and explicitly named `remove-bg` (ONNX model)
as a future tool that does not exist yet.

The architecture has long anticipated `remove-bg` as a planned-but-unbuilt
tool. A test (`tests/unit/test_m6_cloud.py::test_the_tools_the_docs_mention_but_do_not_exist_still_do_not`)
deliberately asserts its absence from the registry — a gate that forces any
future `remove-bg` implementation to be a deliberate act, not an accident.

Separately, there is no existing tool for shrinking image files. The existing
`compress` tool is PDF/Ghostscript-only and cannot take images as input. Adding
a companion `compress-image` tool creates a clear separation: `compress` owns
PDF compression, `compress-image` owns image compression. Both are pure-local
operations today.

## Decision

**Both tools ship with `Engine.LOCAL` only. No cloud engine.**

### `remove-bg`

`remove-bg` removes the background from an image, outputting a transparent PNG.

- **Local engine:** rembg + ONNX model (Python package, lazy-loaded)
- **Cloud engine:** None (reserved for future via separate ADR once local is proven)
- **Rationale:** ADR 0012's gate — implement the local engine first, validate
  it, then decide cloud's pain-relief value by ADR. ONNX model download is a
  genuine "install pain" candidate, but that decision is deferred.
- **Format:** Always outputs PNG (for transparency). Input can be any image
  format `from-images` reads.
- **Dependency:** New `remove-bg` extra in `pyproject.toml` → `rembg>=2.0.0`.

### `compress-image`

`compress-image` shrinks a standalone image file by re-encoding it with
optimized settings. Format is preserved: a JPEG stays a JPEG, PNG uses lossless
optimization, etc.

- **Local engine:** Pillow (already in `images` extra; no new extra needed)
- **Cloud engine:** None (ADR 0034 forbids cloud for millisecond-long pure-Python
  operations; no future ADR will change this)
- **Rationale:** A pure-Python library that runs in milliseconds has no place on
  a cloud service. `compress-image` is pure Pillow — faster and more private
  locally than uploading a document.
- **Format preservation:** Explicitly required by design — use `convert` to
  change formats separately.
- **Dependency:** None (Pillow already in `images` extra).

## Consequences

### For `remove-bg`

- The registry now contains a tool ADR 0012's test deliberately excluded.
  `tests/unit/test_m6_cloud.py::test_the_tools_the_docs_mention_but_do_not_exist_still_do_not`
  is updated to remove `remove-bg`'s absence assertion (only `pdfa` remains).
- Cloud support is explicitly reserved. If/when proposed, it requires a new ADR
  that establishes `remove-bg`'s local engine is proven, its ONNX-download pain
  is "genuinely painful" per ADR 0034, and all costs are weighed.
- A TUI dependency dialog is implemented via the duck-typed
  `missing_dependencies()` method (`core/protocols.py:130-145`), so users see
  "rembg required" rather than a generic error if the package is absent.

### For `compress-image`

- A new tool that owns image compression; `compress` remains PDF-only.
- Pure Pillow implementation lives comfortably in the existing `images` extra.
- `output_required=True` on the ToolSpec enforces format preservation: no
  `compress-image *.jpg` (no default suffix). Format changes are `convert`'s job.

### Shared

- Both tools follow the same generator architecture (rule 1): one CLI command
  each (temporary, until Plan 01 lands), no changes to TUI/server/MCP layers
  (they auto-generate from `ToolSpec`).
- `architecture/overview.md` and `architecture/layers.md` are updated to show
  `remove-bg` as implemented (local-only), `pdfa` still planned.

## Enforcement

- A test asserts `pdfa` remains absent from the registry (unchanged). The
  `remove-bg` absence assertion is removed — its presence is now correct.
- Both tools' specs are validated by the existing hygiene tests:
  - No per-tool code in `cli/`, `server/`, `tui/` (rule 1)
  - No heavy imports at module scope (rule 5)
  - No direct writes (rule 4)
  - No early exits (rule 3)
- No new test gates are needed; the existing contract suite (when it lands —
  ADR 0011, plan 02) will apply automatically.
