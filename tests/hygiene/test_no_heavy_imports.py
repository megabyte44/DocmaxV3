"""Importing the package must not drag in heavy dependencies.

Non-negotiable #3: ``pip install docmax`` gets you the shell and the cloud
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
#:
#: Three kinds of entry, for three different reasons:
#:
#: * **Heavy optional dependencies** (OpenCV, pandas, Pillow…) — the ones that
#:   make an install slow and a wheel large.
#: * **Base dependencies that still belong lower down.** ``pypdf``, ``httpx``
#:   and ``typer`` all ship with the base install, but importing them from a
#:   bare ``import docmax`` would mean the registry is eagerly loading tool
#:   code, or that core has reached for the client or the CLI.
#: * **Interface frameworks that are not installed yet** — ``fastapi``,
#:   ``starlette``, ``uvicorn``, ``mcp``. Listing them costs nothing now and
#:   means the guard already exists on the day those layers land, rather than
#:   being something to remember. A module that is absent simply never appears
#:   in ``sys.modules``, so the check is a no-op until it is not.
#:
#: Nothing here is imported by the test itself, so none of it needs to be
#: installed for the suite to run.
HEAVY_MODULES = (
    # Document processing
    "cv2",
    "numpy",
    "pandas",
    "PIL",
    "pytesseract",
    "pdf2image",
    "pdfplumber",
    "img2pdf",
    "openpyxl",
    "pypdf",
    # Interface frameworks
    "textual",
    "rich",
    "typer",
    "fastapi",
    "starlette",
    "uvicorn",
    "mcp",
    # Transport and cloud SDKs
    "httpx",
    "boto3",
    "botocore",
    "google.cloud",
    "azure",
)

#: Entry points a user or a library actually takes, each of which must stay
#: cheap. ``docmax.core`` is listed separately from ``docmax`` because they can
#: diverge: a heavy import added to a core submodule would not show up in a bare
#: package import if the package does not pull that submodule in.
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
    to fix, where a single aggregate check would only say that something,
    somewhere, got heavier.
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


@pytest.mark.skip(reason="enabled in M1, once core/registry.py exists")
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
