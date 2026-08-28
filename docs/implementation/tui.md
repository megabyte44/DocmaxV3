# The TUI and the visual pickers

What M7 built, and the rules it obeys. For *why* the rules are what they are,
see [ADR 0005](../adr/0005-gui-pickers.md),
[ADR 0019](../adr/0019-picker-package-and-rendering.md),
[ADR 0020](../adr/0020-tui-entry-point.md) and
[ADR 0021](../adr/0021-the-tui-is-generated-from-the-registry.md).

---

## The shape

```
        argv                              keystrokes
         │                                     │
      docmax.cli ─────── entry point ───→ docmax.tui
         │                                     │
         ├────────────→ docmax.pickers ←───────┤     parameters only
         │                     │
         │              tools/_pdf.py (read-only geometry)
         │                                     │
         └──────────→ core.router.EngineRouter ┘
                              │
                    registry → tools → core.atomic
```

Both interfaces call `EngineRouter.run` and nothing else. **There is no routing,
engine resolution, consent rule, validation, atomic write or page count anywhere
in `docmax.tui` or `docmax.pickers`.**

---

## Starting it

| | |
|---|---|
| `docmax tui` | opens it, or explains why it cannot |
| `docmax` (bare) | opens it **only** at an interactive terminal with `textual` installed and no `--json`; otherwise prints help and exits 0 |
| `docmax tui --json` | refused, as one JSON error envelope on stdout |
| `docmax tui` with no TTY | refused, `input.invalid_parameter`, exit 1 |
| `docmax tui` without the extra | refused, `dependency.missing`, naming `pip install "DocmaxV3[tui]"` |

The three guards live in [`cli/main.py`](../../src/docmax/cli/main.py)'s
`_no_command`, and the shared refusal in
[`cli/interactive.py`](../../src/docmax/cli/interactive.py). ADR 0020 records
why each exists.

`textual` is in the `tui` extra, never the base install. Availability is
answered with `importlib.util.find_spec` — never by importing — which is the
same discipline every `EngineStrategy` follows and is why `docmax --help` costs
nothing on a machine with no TUI.

## Inside it

Three screens and two modals, and **no per-tool code at all**:

| | |
|---|---|
| tool list | every offered tool, grouped by `ToolSpec.category` |
| run screen | a form generated from `ToolSpec.params`, plus input, output, engine, force and dry-run |
| consent modal | what `errors.py` has specified since M0 for `ConsentRequiredError` |
| error modal | `code`, `message`, `remedy`. Never a traceback |

`tui/forms.py` maps `Param.type_` — the closed set `str`/`int`/`float`/`bool`/
`path`, plus `choice` when a `Param` declares `choices` — onto widgets, and maps
the filled-in form back to `**params`. It imports no `textual`, which is what
makes the interesting half testable without a terminal.

**An empty optional field is omitted, not passed as `None`.** Every tool reads
`params.get(name, default)`, so an explicit `None` would defeat the tool's own
default — `rotate` would turn pages by 0 degrees instead of 90.

### Which tools appear

Every registered tool except those in `tui/catalog.py`'s `UNIMPLEMENTED`, which
today holds `ocr` alone: it has a full `ToolSpec` and a `run()` that raises until
M8. ADR 0021 explains why that one name is written down, and why the fix is a
Core change deferred rather than made in passing.

### Running

`EngineRouter.run` blocks, so a run happens on a Textual worker thread
(`@work(thread=True)`) and everything it reports comes back through
`call_from_thread`. That is safe by contract, not by care: `ProgressSink` has
required implementations to tolerate a worker thread since M0, and
`CancellationToken` is built on `threading.Event`.

**Cancellation** is `ctrl+c` on the run screen, which calls `token.cancel()`. It
does not raise `KeyboardInterrupt` — Textual owns the keyboard, and the CLI's
`SIGINT` handler is deliberately not reused. The atomic writers then discard the
staged file, so the destination is untouched exactly as on the command line.

---

## The pickers

Two, per ADR 0005, and both follow one rule: **a picker returns parameters,
never results.**

```bash
docmax crop in.pdf -o out.pdf --box 10,10,500,700   # headless: scriptable, SSH-safe
docmax crop in.pdf -o out.pdf --interactive         # picker fills --box, then identical

docmax reorder in.pdf -o out.pdf --order 3,1,2
docmax reorder in.pdf -o out.pdf --interactive
```

The headless form is the tested one. `--interactive` produces a string that is
indistinguishable from one the user typed, and everything after that is the same
code path.

### How it works

A short-lived `http.server` binds an ephemeral port on 127.0.0.1 behind a random
per-run token, serves one HTML page and the document, waits for a POST, and shuts
down. Zero new dependencies and zero vendored assets: the page shows the document
in an `<embed>` using the browser's own PDF viewer, with a transparent drawing
surface over it. ADR 0019 records why not pdf.js.

**The backdrop is a backdrop.** Coordinates come from the drawing surface, sized
to the page's true aspect ratio, so they are exact regardless of how the browser
renders the preview — and the numbers are shown and editable either way.

Where `webbrowser` finds nothing to launch — over SSH, in a bare container — the
URL is printed instead and `--box` / `--order` work regardless.

### Coordinates

`x,y,width,height`, in points (72 to the inch), **origin at the bottom-left** —
the PDF coordinate system's own. A top-left origin reads more naturally to
someone counting down a page in a viewer and was rejected: `crop` writes these
numbers into a `/MediaBox` more or less unchanged, and a flip applied somewhere
in between is invisible in the output and wrong in exactly one direction. The
picker converts for the user, so nobody choosing visually types a coordinate.

### What a picker may not do

Enforced, not intended:

| Rule | What holds it |
|---|---|
| never writes | `pickers` is in `tests/paths.py`'s `LIBRARY_PACKAGES`, so `test_no_direct_writes.py` covers it — plus a test that watches the filesystem across a real interaction |
| never exits | `test_no_sys_exit.py`, same mechanism |
| never imports an engine or the router | `.importlinter`'s `layers` contract |
| never prints | it takes an `announce` callback; the caller decides where a URL goes |

---

## `--json`

`--json` and an interactive session are mutually exclusive, and the refusal is
itself a JSON envelope:

```console
$ docmax tui --json
{"ok": false, "error": {"code": "input.invalid_parameter", "message": "docmax tui cannot be used with --json.", ...}}
```

Same for `--interactive` on `crop` and `reorder`. A Textual app writes a
screenful of escape sequences and a picker prints a URL; both would corrupt the
one object [ADR 0017](../adr/0017-json-output-contract.md) reserves stdout for.
Inside the TUI `--json` has no meaning — the progress sink is a widget and
nothing writes to stdout at all.

---

## `crop`

M7's one new tool, and it exists because ADR 0005 requires a picker's headless
form to ship first. Pure pypdf, local engine only.

It rewrites `/MediaBox` **and** `/CropBox` — a viewer prefers the crop box where
there is one, so writing only the media box produces a file that looks cropped in
some readers and not others.

A page the box does not fit on is **left alone and named in the result**, rather
than produced at the wrong size or used as a reason to fail the other 39. A box
that fits no page at all is refused.

**Cropping moves the page boundary; it does not delete the marks outside it.**
`sanitize` is the tool for removing content. `crop` says so in its help rather
than implying a guarantee it does not make.

---

## Testing

Weighted toward the part that is not a terminal.

| Tier | What |
|---|---|
| pure logic | box parsing, `Param` → field, form → `**params`, payload validation, catalog membership |
| against a fake router | that the right tool is called with the right parameters, that progress and cancellation arrive, that a typed error stays typed |
| real loopback socket | the picker server starts, serves, answers, cancels, times out and shuts down |
| Textual `Pilot` | the app starts, all eighteen tools compose a form, `ctrl+c` cancels, the modals answer |

**No snapshot tests.** ADR 0005's restraint about the pickers applies to the TUI
too: a golden image of a terminal is fragile in the size of the terminal, the
version of the framework and the width of a font, and would fail far more often
for reasons nobody cares about than for reasons anybody does.

Textual tests are gated with `pytest.importorskip("textual")`. CI installs
`[dev,tui]` for the matrix so they actually run, and the `open-core` job stays on
`[dev]` alone — which is what exercises the without-Textual path.
