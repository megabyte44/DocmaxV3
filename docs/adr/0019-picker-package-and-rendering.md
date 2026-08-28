# ADR 0019 — Pickers are their own package below the interfaces, and they render with the browser's own PDF viewer

**Status:** Accepted · 2026-08-28

## Context

[ADR 0005](0005-gui-pickers.md) settled *what* a picker is — "a picker returns
parameters, never results" — and *how* it talks to the user: `http.server` plus
`webbrowser`, both standard library. It left two things unstated that M7 could
not avoid answering.

**Where the code lives.** ADR 0005's examples are all CLI flags
(`docmax crop in.pdf --interactive`), so it never had to say. But the backlog
refers to *"the TUI's visual pickers"*, and both interfaces reaching the same
code is a layering question the moment there are two of them.

The constraints leave very little room:

- Not `core` — it may not import a UI framework, and it cannot open a socket or
  launch a browser.
- Not `tools` — a tool *"may not import any interface"* and must contain no UI.
- Not `cli/` or `tui/` alone — `interfaces-are-independent` forbids either from
  importing the other, so whichever one owned it, the other could not use it.

**How a page gets rendered.** ADR 0005 said "a bundled copy of pdf.js". Acting
on that means vendoring roughly a megabyte of minified JavaScript into the Python
wheel — a file nobody in the project can review, that needs its own licence
record, and that has to be re-vendored on every security advisory.

## Decision

**`src/docmax/pickers/` is its own package, below every interface and above
`tools`.**

```
cli ──┐
      ├──> pickers ──> tools/_pdf.py (read-only geometry) ──> pypdf
tui ──┘
```

It is deliberately **not an interface**. It never prints and never exits: a
caller that wants the URL on screen passes an `announce` callback, which is what
lets the same picker serve the CLI's Rich console and a Textual app that owns
the terminal.

That is not a stylistic preference — it is what allows `pickers` to be listed in
`tests/paths.py`'s `LIBRARY_PACKAGES`, which brings it under
`test_no_direct_writes.py` and `test_no_sys_exit.py`. **ADR 0005's central claim
— "a picker never opens a document for writing, never touches the filesystem"
— is therefore enforced by CI rather than by review.**

The one thing it borrows from below is `tools/_pdf.py`'s read-only geometry
(`open_pdf`, `page_count`, `page_geometry`). A box picker has to know how big
the page is before it can draw one to scale, and asking the module that already
owns "open a PDF the same way in every tool" is better than either a second
pypdf call site or a new contract in `core`.

**Pickers render with the browser's own PDF viewer, not a vendored pdf.js.**

The page puts the document in an `<embed>` and lays a transparent drawing
surface over it. Every browser that can run the page already renders PDFs;
shipping a second renderer to sit next to the first is a cost with no matching
benefit.

**The backdrop is a backdrop.** Coordinates come from the drawing surface, whose
size is fixed to the page's true aspect ratio and whose mapping to points is
therefore exact. If a browser ignores `view=Fit` and renders the preview at some
other zoom, the picture behind the box is wrong but **the numbers are not** — and
the numbers are shown, and editable, so the user is never reduced to guessing.

## Alternatives considered

**Vendor pdf.js, as ADR 0005 said.** Renders identically everywhere and does not
depend on `#view=Fit` behaviour. Rejected on cost: a megabyte of unreviewable
minified JavaScript in the wheel of a tool whose base install is five pure-Python
packages, plus a licence file and a re-vendoring obligation, to improve a
backdrop that does not affect a single returned value.

**Load pdf.js from a CDN.** Smaller wheel, and it defeats the entire premise —
the README's claim is that documents stay on the user's machine and that there
is no browser tab to open and nothing to fetch. Also breaks offline, which is the
one environment the `offline` flag exists to protect.

**Rasterise server-side with Poppler**, which `to-images` already uses.
Rejected: it makes the picker depend on an external binary, which is exactly the
install pain ADR 0005 spent its whole argument avoiding, and a picker that
requires Ghostscript-class dependencies is not an escape hatch.

**Put pickers in `cli/` and give the TUI none.** Simplest, and matches ADR
0005's examples literally. Rejected because the backlog already refers to the
TUI's pickers, and because moving the package later would be a layering change
rather than a refactor.

**Terminal graphics inside Textual** — already rejected by ADR 0005 for working
"beautifully in two terminals and not at all in the rest", and nothing has
changed.

## Consequences

- **Zero new dependencies**, as ADR 0005 promised, and zero vendored assets —
  which is better than it promised.
- A new package in the layer diagram, and a new line in `.importlinter`. That is
  the cost of two interfaces sharing anything.
- The reorder picker shows numbered cards rather than page thumbnails, because
  thumbnails need one of the rejected renderers. The document is displayed
  alongside for reference. This is a genuine loss and it is the price of the
  dependency rule.
- The crop backdrop's alignment depends on browser handling of
  `#view=Fit&toolbar=0`. Where that is imperfect the coordinates remain correct
  and the numeric fields remain authoritative, but the experience is worse in
  some browsers than in others.
- The `--box` and `--order` flags remain the tested, scriptable, SSH-safe path.
  Nothing in the project's behaviour depends on a browser existing.

## Enforcement

- `tests/paths.py` lists `pickers` in `LIBRARY_PACKAGES`, so
  `test_no_direct_writes.py` fails the build on any write outside
  `core/atomic.py`, and `test_no_sys_exit.py` on any process exit.
- `tests/unit/test_pickers.py::test_a_picker_writes_nothing` watches the
  filesystem across a real loopback interaction.
- `.importlinter`'s `layers` contract places `docmax.pickers` below every
  interface and above `docmax.tools`, so nothing in `tools`, `cloud_client` or
  `core` can import it and it cannot import an interface.
- The picker package imports no `EngineStrategy`, no router and no
  `atomic_write`; the layers contract makes the first two unreachable and the
  hygiene test makes the third pointless.
