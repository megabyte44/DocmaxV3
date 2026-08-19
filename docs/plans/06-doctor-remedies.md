# Plan 06 — Make the missing-binary path a copy-paste

**Status: mostly done.** The M1–M3 stack (PR #10) built the substance of this
plan. What follows is what it did, and the four things left.

For a tool that shells out to Ghostscript, Tesseract, Poppler, and Pandoc, the
thing that loses users is not speed. It is `tesseract is not installed` on a
fresh machine, followed by twenty minutes of searching.

---

## 1. What the stack already built

`tools/_binaries.py` is close to what this plan specified, and in one respect
better:

- a `Binary` record with `name`, `used_by`, `commands`, and per-platform
  `install` lines
- `install_hint()` returning the command for *this* platform, falling back to
  every platform's line when it cannot tell
- `find`, `require`, `run`, and `describe`, with a timeout on every call taken
  from the cancellation token
- `commands` as a tuple rather than a string, because Ghostscript is `gs` on
  Unix and `gswin64c` on Windows — and the *console* build specifically, since
  bare `gswin64` opens a window and never returns

That last detail is the kind of thing this plan would not have caught. Take the
implementation as it stands.

## 2. Layering: resolved differently, and correctly

This plan originally proposed moving the tool→binary mapping onto
`ToolSpec.requires_binaries` and deleting the central table. The stack instead
moved the table from `cli/main.py` down to `tools/_binaries.py`, which fixes the
real defect — `tools` sits below `cli` in the layering, so engines could not
reach the map that `doctor` was reporting from.

It also gave a reason to keep the list central that this plan did not consider:
it declares `pdfa`, `to-images`, and `convert`, which do not exist yet, so
`doctor` can report on the whole roadmap rather than only on what is built.

The remaining half of the rectification is carried in
[plan 01, R1](01-spec-driven-surfaces.md): keep the catalogue, add
`ToolSpec.requires_binaries` so a tool declares its own dependency, and add a
hygiene test asserting the two agree for every registered tool. Roadmap
coverage and no drift.

## 3. What is left

### 3.1 Wire the hint into the failure, not only into `doctor`

`LocalDependencyMissingError` takes an `install_hint` and prefers it as the
remedy, and `Binary.install_hint()` now produces a real one. Confirm every
raise site passes it, so the user sees:

```
Not installed: tesseract, pdftoppm

  brew install tesseract
  brew install poppler

Or run with --engine cloud to skip the install entirely.
```

The user should never have to run `doctor` to find out what to type. `doctor`
is where you go for the whole picture, not where you are sent after a failure.

### 3.2 `doctor --json`

Same envelope discipline as [plan 04](04-json-envelope.md): status per binary,
version found, path, dependent tools, and the install command for this machine.
Consumed by `setup`, by the server's capabilities endpoint, and by anyone
scripting a deployment.

### 3.3 Check more than PATH

Each of these is a real support question:

- **version floors** — Ghostscript below 9.50 has known PDF/A problems
- **Tesseract language packs** — `tesseract --list-langs`; the binary present
  while `eng` is missing is a genuinely confusing failure
- **the Python side too** — report the extras (`ocr`, `tables`, `images`,
  `tui`) as found or missing in the same table. A user does not distinguish
  "the binding is missing" from "the binary is missing", and should not have to

### 3.4 Test the install table

`test_binaries.py` exists. Extend it with one case per platform, with
`sys.platform` patched, asserting `install_hint()` returns the right line —
including the unrecognised-platform fallback.

## 4. Acceptance

- [ ] every `LocalDependencyMissingError` carries a per-platform install command
- [ ] `doctor --json` emits the machine-readable form
- [ ] language packs, version floors, and Python extras appear in the table
- [ ] `install_hint()` has a test per platform, with the detector patched
- [ ] `ToolSpec.requires_binaries` agrees with `Binary.used_by` (plan 01, R1)
