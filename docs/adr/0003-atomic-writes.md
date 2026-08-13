# ADR 0003 — All output is written atomically, and destinations are types

**Status:** Accepted · 2026-08-13

## Context

v2 had no atomic write anywhere. Output went straight to its destination via
`open(out, "wb")`, `Path.write_text`, or `cv2.imwrite`. Three failures shipped
to users:

1. A crash or Ctrl-C mid-write left a truncated, unopenable file.
2. Re-running any command silently destroyed the previous run's output — no
   existence check, no prompt, no `--force`.
3. Several paths wrote *over the input*. `docmax convert x.md --to md` destroyed
   the source file. `batch_convert` over a directory of `.md` files did it in
   bulk. OCR of a `.txt` to `.txt` overwrote itself.

The third is the worst: silent, irreversible, and reachable from documented
commands.

## Decision

Two mechanisms, both enforced by tests rather than review.

**1. `OutputTarget` makes in-place overwrite unrepresentable.**

No engine ever receives a raw destination `Path`. Every write goes through:

```python
OutputTarget.resolve(inputs=[...], requested=..., default_suffix=".pdf", force=False)
```

which raises `InPlaceOverwriteError` if the destination is any input, and
`OutputExistsError` if it exists without `force`. Engine signatures accept
`OutputTarget`, so passing a `Path` is a mypy error under `strict`. v2's
`input_path.with_suffix(...)` idiom is not expressible.

**2. `core/atomic.py` is the only module that may write.**

```python
atomic_write(target, *, validators=())   # Python writes the bytes
atomic_path(target, *, validators=())    # a subprocess writes the file (Ghostscript, Pandoc)
atomic_dir(target, *, validators=())     # multi-file output (split, to-images)
```

Each creates a temp file *on the same filesystem as the destination* (so the
final step is a rename, not a cross-device copy), runs the operation's
validators against it, then `os.replace()`. That call is atomic on both POSIX
and Windows. Any exception deletes the temp and leaves the destination
untouched.

`tests/hygiene/test_no_direct_writes.py` walks the AST of `core`, `tools`, and
`cloud_client` looking for `open(..., "w")`, `write_text`, `write_bytes`,
`cv2.imwrite`, and `shutil.move`, and fails on any hit outside `atomic.py`.

## Consequences

- The guarantee is total: a crash mid-operation leaves the destination either
  absent or unchanged, never partial. Verified by a test that raises inside the
  context manager and asserts both the destination and the temp directory.
- Validators become a first-class per-tool concern — "did merge produce a PDF
  whose page count equals the sum of its inputs?" runs *before* the swap, so a
  wrong result is never delivered at all.
- Cost: writes need a same-filesystem temp location, which means output onto a
  full volume fails at validate time rather than mid-write. That is the correct
  trade — `InsufficientDiskSpaceError` beats a half-written file.
- Cost: `atomic_dir` has to stage a whole directory and swap it. For `split` on
  a 1000-page document that is real temp space. Accepted; the alternative is
  partial output on cancellation.
