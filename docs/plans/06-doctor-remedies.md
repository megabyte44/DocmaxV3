# Plan 06 — Make the missing-binary path a copy-paste

**Goal:** the most common first-run failure of a local-first tool stops being a
dead end.

For a tool that shells out to Ghostscript, Tesseract, Poppler, and Pandoc, the
thing that loses users is not speed. It is `tesseract is not installed` on a
fresh machine, followed by twenty minutes of searching. Half a day of work
converts that into a copy-paste.

---

## 1. Rectification: `doctor` should not own the list

Covered as R1 in [plan 01](01-spec-driven-surfaces.md) — `EXTERNAL_BINARIES` in
`cli/main.py:31` moves onto `ToolSpec.requires_binaries`, and `doctor` inverts
the registry. This plan assumes that has landed.

## 2. `core/binaries.py`

One module, no UI imports, usable by `doctor`, `setup`, the error path, and the
server's capabilities endpoint.

```python
@dataclass(frozen=True, slots=True)
class Binary:
    name: str
    purpose: str
    #: Platform -> the command that installs it. Keyed by the package manager
    #: actually present, not by OS alone: a macOS user without Homebrew needs
    #: a different answer from one with it.
    install: Mapping[str, str]
    #: Argv that prints a version, for `doctor` and for engine_version.
    version_argv: tuple[str, ...]
```

```python
def install_hint(binary: str) -> str:
    """The exact command for *this* machine, or the download page if unknown."""
```

Detection order: `brew` / `port` on macOS; `apt` / `dnf` / `pacman` / `zypper`
on Linux; `winget` / `choco` / `scoop` on Windows. Falling back to a URL is a
fine outcome — being wrong about the package manager is not.

## 3. Wire it into the failure, not just into `doctor`

`LocalDependencyMissingError` already takes an `install_hint` and already
prefers it as the remedy. Today `ocr/local.py` supplies a generic string:

```python
INSTALL_HINT = f"Run `{CLI_NAME} setup --ocr`, or use --engine cloud to skip the install."
```

That is the right shape and the wrong specificity. The router should build the
remedy from `install_hint()` for the binaries that are actually missing:

```
Not installed: tesseract, pdftoppm

  brew install tesseract poppler

Or run with --engine cloud to skip the install entirely.
```

The user never has to run `doctor` to find out what to type. `doctor` becomes
the place you go when you want the *whole* picture, not the place you are
forced to go after every failure.

## 4. `doctor --json`

The same envelope discipline as [plan 04](04-json-envelope.md): status per
binary, the version found, the path, which tools need it, and the install
command for this machine. Used by `setup`, by the server's capabilities
endpoint, by CI, and by anyone scripting a deployment.

## 5. While in here: `doctor` should check more than PATH

Cheap additions, each of which is a real support question:

- **version floors** — Ghostscript below 9.50 has known PDF/A problems
- **Tesseract language packs** — `tesseract --list-langs`; the binary being
  present while `eng` is missing is a genuinely confusing failure
- **the Python side too** — report the extras (`ocr`, `tables`, `images`,
  `tui`) as found or missing, in the same table. A user does not distinguish
  "the binding is missing" from "the binary is missing", and should not have to.

## 6. Acceptance

- [ ] `EXTERNAL_BINARIES` is gone; `doctor` derives everything from the registry
- [ ] `doctor` prints a per-machine install command for each missing binary
- [ ] a missing-dependency error carries that same command as its remedy
- [ ] `doctor --json` emits the machine-readable form
- [ ] language packs and extras appear in the table
- [ ] the install-command table has a unit test per platform, with the detector patched
