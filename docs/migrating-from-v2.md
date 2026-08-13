# Migrating from DocMax 2.x

**Short version:** your commands still work. Two behaviours changed, both
because the old ones could destroy your files.

## Command names are unchanged

Every v2 command keeps its name and its flags. `--engine` is new and optional,
defaulting to `auto`, which picks local when your dependencies are installed.

```bash
docmax merge a.pdf b.pdf -o out.pdf     # identical to v2
docmax ocr scan.pdf --lang eng+hin      # identical to v2
docmax ocr scan.pdf --engine cloud      # new, optional
```

## What changed

### Existing files need `--force`

**v2:** writing to an existing path silently overwrote it. Running a command
twice destroyed the first run's output.

**v3:** raises `output.exists`. Pass `--force` if you meant it.

```bash
docmax compress big.pdf -o small.pdf            # fails if small.pdf exists
docmax compress big.pdf -o small.pdf --force    # overwrites deliberately
```

If you have scripts that rely on overwriting, add `--force`. This is the change
most likely to affect automation.

### An input can no longer be the output

**v2:** `docmax convert notes.md --to md` read `notes.md`, wrote to
`notes.md`, and destroyed it. The same applied to batch conversion over a
directory of same-extension files, and to OCR of a `.txt` file to `.txt`.

**v3:** raises `output.in_place_overwrite` and touches nothing.

There is no flag to re-enable this. Write to a different path and move the file
yourself if that is genuinely what you want.

### Python 3.11 minimum

v2 supported 3.9+. If you are on 3.9 or 3.10, either upgrade Python or pin
`docmax<3`. Both versions are near end-of-life; see
[ADR 0001](adr/0001-python-311.md) for what 3.11 buys.

### Errors, if you import DocMax as a library

**v2:** operation modules called `sys.exit(1)` on failure, which made them
effectively unusable outside the CLI.

**v3:** raises typed exceptions from `docmax.core.errors`.

```python
from docmax.core.errors import DocMaxError, EncryptedDocumentError

try:
    ...
except EncryptedDocumentError as exc:
    print(exc.remedy)  # actionable next step
except DocMaxError as exc:
    print(exc.code, exc.message)
```

`DocMaxError` catches everything DocMax raises deliberately.

### Config location

v2 used `~/.docmax/config.json` (and created an unused `~/.DocMax/`). v3 uses
the platform config directory via `platformdirs`, in TOML.

Run `docmax config path` to see where yours is. Tool paths are re-discovered
automatically by `docmax doctor`, so nothing needs to be migrated by hand.

## Things that got quietly fixed

If you noticed any of these in v2, they are gone: extracted images that would
not open, deskew rotating pages the wrong way, OCR settings that appeared to
save but did nothing, table extraction ignoring the format you asked for,
`_preprocessed.png` files accumulating next to your documents, and folder-watch
mode processing its own output in a loop.

See the [changelog](../CHANGELOG.md) for the full list.
