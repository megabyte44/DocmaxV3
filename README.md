# DocMax

**A document toolkit that lives in your terminal.** Merge, split, OCR, compress,
convert, redact — locally, privately, with no server to run and no browser tab
to open.

```bash
pip install DocmaxV3
docmax merge a.pdf b.pdf -o combined.pdf
```

> **Status: early development (M0).** The architecture and safety mechanisms are
> in place; the tools are being rebuilt on top of them one at a time. For a
> working tool today, use [`docmax` 2.x](https://pypi.org/project/docmax/).
> See [the roadmap](#roadmap) for what lands when.

---

## Why another PDF tool

The good self-hosted options — Stirling PDF and friends — are excellent, and
they all assume a browser. That means Docker, a running server, a port, and no
sensible way to use them over SSH or from a script.

DocMax assumes a terminal instead.

|  | DocMax | Self-hosted web tools |
|---|---|---|
| Install | `pip install DocmaxV3` | Docker + a container |
| Interface | CLI and TUI | browser |
| Over SSH | works | needs port forwarding |
| Scripting | argv | HTTP against a running server |
| Your documents | stay on your machine | stay on your machine |

## Two engines, one interface

Every operation can run two ways, and the choice is yours per tool:

- **Local** — offline and private. Needs the relevant dependencies installed.
- **Cloud** — no local install at all. For the handful of tools whose
  dependencies are genuinely painful.

```bash
docmax ocr scan.pdf                     # picks whichever is available
docmax ocr scan.pdf --engine local      # force local
docmax ocr scan.pdf --engine cloud      # skip installing Tesseract
```

Cloud exists for exactly one reason — to let you use a tool without installing
its heavy dependencies. Only a handful of tools have it — **`compress` and
`convert` today** — because for a pure-Python operation like `merge`, uploading
your document would be slower, less private, and pointless. OCR's cloud engine
arrives with OCR itself, at M8.

```bash
docmax cloud login          # store an API key
docmax cloud status         # endpoint, key, and what you have agreed to send
docmax compress big.pdf -o small.pdf --engine cloud
```

**Nothing is ever uploaded without asking.** Consent is per-tool and remembered;
`offline = true` in your config disables cloud entirely regardless of flags; and
every upload tells you what it is sending before it sends it. The cloud endpoint
is configurable, so you can point DocMax at your own server instead.

## Your files are safe

This is the part most tools get wrong, so it is worth being specific.

- **Atomic writes.** Output goes to a temp file, gets validated, and is only then
  swapped into place. A crash or Ctrl-C mid-operation leaves your destination
  either untouched or absent — never half-written.
- **Your input is never the output.** `docmax merge a.pdf b.pdf -o a.pdf` is
  refused, not silently obeyed.
- **Nothing is overwritten by accident.** Existing files need `--force`.
- **No tracebacks.** Every anticipated failure gives you a plain message and the
  next step to take.

These are enforced by tests that run on every commit across Linux, macOS, and
Windows — not by good intentions. See
[architecture.md](docs/architecture/overview.md#the-structural-guarantees).

## Install

```bash
pip install DocmaxV3              # the shell and the cloud client
pip install "DocmaxV3[ocr]"       # local OCR
pip install "DocmaxV3[crypto]"    # AES encryption for `protect`
pip install "DocmaxV3[all]"       # everything
```

The base install is deliberately small. Heavy dependencies arrive only when you
first ask for a local engine that needs them.

Some local engines also need external programs. `compress` needs
**Ghostscript**; OCR and conversion will need Tesseract, Poppler and Pandoc.

`protect` defaults to AES-256, which needs the `crypto` extra. It says so and
names the install line rather than quietly falling back to RC4 — a tool called
`protect` should not hand you broken encryption without mentioning it.

`convert` needs **Pandoc**, and `to-images` needs **Poppler**.

```bash
docmax formats     # what every tool can read and write
```

**`convert` does not handle PDF in either direction.** Pandoc has no PDF reader,
and writing PDF needs a LaTeX distribution DocMax does not install — so
`convert report.pdf --to docx` is refused with an explanation rather than a bad
answer. It converts between Markdown, HTML, Word, OpenDocument,
reStructuredText, LaTeX source, EPUB and plain text. To turn a PDF into images,
use `to-images`. See
[ADR 0011](docs/adr/0011-convert-is-pandoc-only.md).

```bash
docmax doctor      # what's installed, what's missing, and the command to fix it
```

## Many documents, several steps, or a folder that fills up

```bash
docmax batch scans/*.pdf --output-dir out --tool ocr
docmax pipeline scan.pdf --pipeline clean.toml -o clean.pdf
docmax watch inbox --output-dir done --tool ocr
```

A **pipeline** chains operations over one document. The stages live in a TOML
file, so a workflow is something you save and re-run rather than retype:

```toml
name = "scan-cleanup"

[[stage]]
tool = "ocr"
params = { lang = "eng", dpi = 300 }

[[stage]]
tool = "compress"
params = { preset = "ebook" }
```

**Only the last stage writes your file.** The intermediate documents live in one
temporary directory and are gone whether the run succeeded, failed or was
interrupted — so a failure at stage three leaves your destination exactly as it
was, and nothing is ever left lying beside your documents.

A **batch** runs one operation over many documents, naming each output after its
input. One corrupt file does not cost you the other hundred and ninety-nine: it
is reported and the rest carry on. Two things are refused before any work
starts, because neither can be undone afterwards — two inputs whose names would
collide in the output directory, and any output that would land on an input.

A **watch** processes documents as they arrive in a folder. A file is picked up
only once it has stopped changing, so a document still being copied in is left
alone until it is whole, and each one is handled exactly once.

**`--output-dir` may not be inside the folder you are watching.** v2's watcher
wrote its output beside its input, saw that output as new input, and fed on
itself. That is now refused rather than survived. See
[ADR 0026](docs/adr/0026-the-watcher-polls-and-never-watches-its-own-output.md).

**There is no `--resume` yet.** The roadmap says "resumable batch"; a resume
journal is a persistent file format that deserves deciding on its own, so it was
deferred rather than improvised. Re-running an interrupted batch repeats what
already succeeded, safely — the outputs exist, and DocMax refuses to overwrite
them without `--force`.

## Drive it from an AI agent

```bash
pip install "DocmaxV3[mcp]"
docmax mcp --root ~/Documents
```

Serves every tool over the Model Context Protocol on stdio, so an assistant can
merge, split, compress or OCR your documents — **on your machine, with nothing
uploaded**. Point your MCP client at it:

```json
{
  "mcpServers": {
    "docmax": { "command": "docmax", "args": ["mcp", "--root", "/home/you/Documents"] }
  }
}
```

The tool list is generated from the same registry the CLI reads, so an agent sees
exactly what you can run, with the same parameters and the same validation.

**An agent is not a person, and it is not trusted like one.**

- **It can only touch `--root`.** Reads and writes outside it are refused before
  anything runs — `..`, symlinks and lookalike directory names included. The
  default is the directory you started the server in.
- **It cannot overwrite your files.** There is no `--force` to give it; an
  existing destination is an error.
- **It cannot upload anything.** Cloud engines are off unless you pass
  `--allow-cloud`, and even then only for tools *you* already agreed to with
  `docmax cloud agree`. An agent cannot consent on your behalf, and a configured
  `offline = true` cannot be overridden by a flag.
- **It gets no shell, no filesystem browsing, and no tracebacks.**

Cancelling a request cancels the underlying operation, and the atomic writes mean
a cancelled run leaves your destination exactly as it was. See
[docs/implementation/mcp.md](docs/implementation/mcp.md).

## An interface for when you are not scripting

```bash
pip install "DocmaxV3[tui]"
docmax tui        # or just `docmax`, at a terminal
```

Every tool, the same router, the same engines — a second way in, not a second
implementation. Pick a tool, fill in the form, watch the progress, press
`ctrl+c` to stop. It is generated from the tool registry, so it always offers
exactly what the CLI does.

Two operations need a value a terminal cannot ask for — where to crop, and what
order pages go in. Those get a browser tab:

```bash
docmax crop scan.pdf -o trimmed.pdf --box 36,36,540,720   # scriptable
docmax crop scan.pdf -o trimmed.pdf --interactive         # drag a box instead

docmax reorder in.pdf -o out.pdf --order 3,1,2
docmax reorder in.pdf -o out.pdf --interactive
```

**The picker returns the parameter and nothing else.** It never opens your
document for writing and has no route to an output file. The flag form is the
one that is tested, works over SSH, and is what `--interactive` fills in — so
nothing you can do in a browser is something you cannot do in a script.

`doctor` prints the install line for your platform — `apt install ghostscript`,
`brew install ghostscript`, or the winget package on Windows. It only reports;
nothing is installed for you.

## Roadmap

| | | |
|---|---|---|
| **M0** | Foundation — architecture, CI, safety mechanisms | ✅ done |
| **M1** | Core engine + `merge` as the reference implementation | ✅ complete |
| **M2** | `split`, `rotate`, `reorder`, `pages`, `metadata`, `sanitize`, `get-info` | ✅ done |
| **M3** | `compress` + external-binary support in `doctor` | ✅ done |
| **M4** | `watermark`, `stamp`, `protect`, `unlock`, `permissions` | ✅ done |
| **M5** | `convert`, `to-images`, `from-images` | ✅ done |
| **M6** | Cloud engines, `--json` everywhere, published benchmarks | ✅ done |
| **M7** | Textual TUI + visual pickers for crop and reorder | ✅ done |
| **M8** | OCR, done properly | ✅ done |
| **M9** | Pipelines, resumable batch, folder watch | ✅ |
| **M10** | Local MCP server — drive DocMax from an AI agent, nothing leaves your machine | ✅ |

Benchmarks live in [`benchmarks/`](benchmarks/METHODOLOGY.md) with the method
written down. Run them with `python -m benchmarks`. No numbers appear in this
README until they are measured — and none have been yet.

## Documentation

[**docs/**](docs/README.md) is the index. The short version:

- [architecture/overview.md](docs/architecture/overview.md) — how DocMax is put
  together, and why
- [adr/](docs/adr/README.md) — the decisions, and what they cost
- [planning/current-status.md](docs/planning/current-status.md) — what is done,
  what is next, what is missing

## Contributing

```bash
git clone https://github.com/megabyte44/docmax
cd docmax
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
pre-commit install

pytest && ruff check . && mypy && lint-imports
```

Start with [docs/architecture/overview.md](docs/architecture/overview.md) and the
[ADRs](docs/adr/) — they explain the constraints, most of which exist for a
specific reason.

## Licence

MIT. Every document operation is free and always will be — see
[ADR 0004](docs/adr/0004-open-core-boundary.md) for where the open-core line
sits and why.
