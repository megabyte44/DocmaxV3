"""Importing the package must not drag in heavy dependencies.

Non-negotiable #3: ``pip install DocmaxV3`` gets you the shell and the cloud
client. OpenCV, pandas, Pillow, and the Tesseract bindings arrive only when a
user first invokes a local engine that genuinely needs them.

That promise is easy to state and easy to break — one convenience import at
module scope in a tool package undoes it silently, and nobody notices until
startup is slow and the wheel is 200MB. So it is asserted, not documented.

The check runs in a subprocess: once a heavy module is in this process's
``sys.modules`` (pytest itself, or another test, may have imported it) an
in-process assertion proves nothing.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

#: Modules that must not appear after a bare ``import docmax``.
#: ``pypdf`` is included deliberately: it is a base dependency, but it belongs
#: inside tool implementations, which the registry loads lazily. Its presence
#: here would mean the registry is eagerly importing tool code.
HEAVY_MODULES = (
    "cv2",
    "numpy",
    "pandas",
    "PIL",
    "pytesseract",
    "pdf2image",
    "pdfplumber",
    "img2pdf",
    "openpyxl",
    "textual",
    "pypdf",
    "rich",
    "typer",
    "httpx",
    # The server's own dependencies. A user running `docmax merge` has no web
    # framework installed and never needs one; if either of these ever appears
    # after a bare import, the interface boundary has leaked downward.
    "fastapi",
    "uvicorn",
)

_PROBE = textwrap.dedent(
    """
    import sys
    import docmax

    heavy = {heavy!r}
    found = sorted(m for m in heavy if m in sys.modules)
    if found:
        print(",".join(found))
        sys.exit(1)
    """
)


def _probe(snippet: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_bare_import_pulls_in_nothing_heavy() -> None:
    result = _probe(_PROBE.format(heavy=HEAVY_MODULES))

    assert result.returncode == 0, (
        "`import docmax` pulled in heavy dependencies: "
        f"{result.stdout.strip()}\n"
        "Move these imports inside the functions that need them. "
        "The base install must stay light — see docs/adr/0001 and non-negotiable #3.\n"
        f"{result.stderr}"
    )


def test_version_is_available_without_heavy_imports() -> None:
    """``docmax --version`` is the fastest path a user can take. Keep it fast."""
    result = _probe("import docmax; print(docmax.__version__)")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "no version reported"


def test_building_the_registry_pulls_in_nothing_heavy() -> None:
    """The registry knows every tool's name and params without importing local.py.

    This is what makes tool #51 free: discovery is metadata-only, so listing
    tools never costs an OpenCV import.
    """
    result = _probe(
        "from docmax.core.registry import build_registry; build_registry()\n"
        + _PROBE.format(heavy=HEAVY_MODULES)
    )
    assert result.returncode == 0, result.stdout
