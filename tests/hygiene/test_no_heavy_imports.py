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

import pytest

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
    "starlette",
    # The M10 MCP server, listed before it exists. A forbidden name costs
    # nothing while the package is absent — it simply never appears in
    # ``sys.modules`` — so the guard is in place on the day the layer lands
    # rather than being something to remember then.
    "mcp",
    # Cloud SDKs. The cloud client speaks HTTP directly and needs none of these;
    # one appearing here would mean a provider dependency had been introduced
    # below the interface layer.
    "boto3",
    "botocore",
    "google.cloud",
    "azure",
)

#: Entry points a user or a library actually takes, each of which must stay
#: cheap. ``docmax.core`` and its submodules are listed separately from
#: ``docmax`` because they can diverge: a heavy import added to a core submodule
#: would not show up in a bare package import that never pulls that submodule
#: in.
LIGHTWEIGHT_IMPORTS = (
    "docmax",
    "docmax.core",
    "docmax.core.models",
    "docmax.core.protocols",
    "docmax.core.errors",
    "docmax.core.atomic",
    "docmax.core.cancellation",
    "docmax.core.config",
    "docmax.core.consent",
    "docmax.core.registry",
)

_PROBE = textwrap.dedent(
    """
    import sys
    import {module}

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


@pytest.mark.parametrize("module", LIGHTWEIGHT_IMPORTS)
def test_import_pulls_in_nothing_heavy(module: str) -> None:
    """Each entry point stays cheap, checked one at a time.

    Per-module rather than one combined probe, because the failure message is
    the point: "importing docmax.core.consent pulled in httpx" names the module
    to fix, where an aggregate check would only say that something, somewhere,
    got heavier.
    """
    result = _probe(_PROBE.format(module=module, heavy=HEAVY_MODULES))

    assert result.returncode == 0, (
        f"`import {module}` pulled in heavy dependencies: "
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
        + _PROBE.format(module="docmax", heavy=HEAVY_MODULES)
    )
    assert result.returncode == 0, result.stdout
