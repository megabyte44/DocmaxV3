# ADR 0005 — GUI escape hatches are parameter pickers served over localhost

**Status:** Accepted · 2026-08-13

## Context

A few operations need a coordinate that is genuinely unguessable without seeing
the page: where to crop, which region to redact, what order pages should go in.
A terminal cannot express "drag a rectangle around this paragraph."

But a GUI is also how a focused terminal tool turns into an unfocused desktop
app. The constraint is explicit: a narrow escape hatch for spatial interaction,
never a general GUI shell.

## Decision

**A picker returns parameters, never results.**

The crop window does not crop. It renders the page, the user drags a box, and
it returns `(x, y, width, height)`. That value then travels the normal
`Tool` → `EngineRouter` → `atomic_write` path like any other parameter. A picker
never opens a document for writing, never touches the filesystem, and never
imports an engine.

This is what makes "never a general GUI shell" structural rather than a promise:
a parameter picker has nowhere to grow. There is no code path from a picker to
an output file.

**Every picker has a headless equivalent, and the headless form ships first.**

```bash
docmax crop in.pdf --box 10,10,500,700     # scriptable, testable, works over SSH
docmax crop in.pdf --interactive           # picker fills --box, then proceeds identically
```

The operation is tested headlessly. The picker needs no tests beyond "returns a
well-formed box", and it is never on the critical path.

**Three pickers only: `crop`, `redact`, `reorder`.** Watermark and stamp take
named positions (`--position bottom-right`) and do not qualify. Visual redaction
complements pattern redaction (`--pattern "\d{3}-\d{2}-\d{4}"`) rather than
replacing it; the pattern form is primary because it batches.

**Implementation: `http.server` + `webbrowser`, both stdlib.**

A short-lived local server renders a page with a bundled copy of pdf.js. The
browser POSTs coordinates back, the server shuts down, the function returns.

Rejected alternatives:

- **pywebview** — depends on platform webview runtimes and is a leading source
  of Linux install failures. Also a new base dependency, which non-negotiable #3
  forbids.
- **PyQt / PySide** — a ~60MB wheel plus a licensing discussion, for a
  four-second interaction.
- **Terminal graphics (Kitty/sixel) inside Textual** — works beautifully in two
  terminals and not at all in the rest. Too fragile to be the only path.

## Consequences

- Zero new dependencies. The picker costs one HTML file and ~100 lines of
  server code.
- Over SSH the picker degrades to printing a URL, and the `--box` form works
  regardless. Neither is a broken experience.
- The trade-off is honest: a browser tab, not a native window. For "drag a box
  and press enter" that is acceptable.
- Pickers land in M7 alongside the TUI. The headless flags ship in M2, so
  nothing waits on them.
