# Plan 03 — Keep `-` representable

**Goal:** spend half a day at M1 so that M9 pipelines are a feature rather than
a rewrite of every engine signature.

**Do not build streaming now.** Build the *types* that allow it, so the fifty
engines written between here and M9 have signatures that already accommodate
it.

---

## 1. The lock-in

Today:

- `DocumentRef.from_path` requires `resolved.is_file()` — `models.py:74`
- `OutputTarget.resolve` requires an existing parent directory — `models.py:128`

So this is not merely unimplemented, it is **unrepresentable**:

```bash
docmax merge a.pdf b.pdf -o - | docmax compress - -o small.pdf
cat scan.pdf | docmax ocr - -o out.pdf
```

Pipelines are M9. By then, every engine will have been written against
`DocumentRef.path` and `OutputTarget.destination` as real filesystem paths, and
adding a stream variant means revisiting all of them.

This matters more than it looks. The README's differentiator table says
"Scripting: argv" — but the thing a browser tool structurally *cannot* do is
compose. `docmax` in a shell pipeline is the demo that explains the whole
product in one line.

## 2. The design

Two named types and one context manager. No behaviour change at M1.

```python
# core/models.py


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """A document we were asked to read.

    `path` is None exactly when the source is a stream. Engines must not read
    `path` directly — see `materialize`.
    """

    path: Path | None
    stream: BinaryIO | None = None

    @contextmanager
    def materialize(self) -> Iterator[Path]:
        """Yield a real path for this source, spilling a stream to temp.

        Most engines shell out to Ghostscript or Tesseract and need a genuine
        file. Those call `materialize`. Engines that can consume bytes
        directly may read `stream`, but nothing is required to.
        """
```

```python
@dataclass(frozen=True, slots=True)
class OutputTarget:
    destination: Path | None  # None means stdout
    force: bool = False

    @property
    def is_stream(self) -> bool:
        return self.destination is None
```

`atomic.py` gains a stdout sink that preserves the existing semantics exactly:
stage to a temp file, run the validators, and only then copy the bytes to
stdout. Nothing is emitted unless it would have been swapped into place. A
consumer downstream in the pipe therefore never receives a partial document —
the same guarantee, over a pipe.

## 3. What lands at M1 (half a day)

- `path: Path | None` on `DocumentRef`, with `materialize()` implemented for
  the file case only and raising `NotImplementedError` for streams.
- `destination: Path | None` on `OutputTarget` and the `is_stream` property.
  `resolve()` returns a stream target for `-` and keeps every existing check
  for real paths.
- **A rule, enforced:** engines call `ref.materialize()`, never `ref.path`.
  Add it to `tests/hygiene/test_no_direct_writes.py`'s sibling — an AST walk of
  `docmax/tools` failing on any `.path` attribute access on a `DocumentRef`.
  Written now, it costs nothing; written at M9, it is a fifty-file migration.
- Contract-suite additions ([plan 02](02-contract-test-suite.md)): a stream
  target is *rejected cleanly* today, with a typed error rather than an
  `AttributeError` on `None`.

## 4. What lands at M9

Stream spilling in `materialize`, the stdout sink in `atomic.py`, `-` accepted
in argv, and progress redirected to stderr whenever the output is stdout.

By then the signatures already fit, and no engine changes.

## 5. Open question to settle now, not later

**Does `-o -` disable the atomicity guarantee?** No — see §2. Staging still
happens; only the final `os.replace` becomes a copy to a stream. Worth stating
explicitly in ADR 0003's consequences when this lands, because it is the first
thing a reviewer will ask.

## 6. Acceptance

- [ ] `DocumentRef.path` is `Path | None`; `materialize()` exists and is used
- [ ] `OutputTarget.destination` is `Path | None`; `is_stream` exists
- [ ] `docmax merge a.pdf b.pdf -o -` fails with a typed "not yet supported" error
- [ ] mypy strict passes with the widened types (this is most of the work)
- [ ] the hygiene test forbidding `ref.path` in `docmax/tools` passes
