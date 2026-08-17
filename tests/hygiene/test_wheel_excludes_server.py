"""The wheel a user installs does not carry the server.

``pip install docmax`` should get someone a terminal tool. The Cloud Engine's
server half is deployed from a checkout, inside an image that also carries
Ghostscript, Tesseract, and Pandoc — none of which pip can install — so shipping
its source to every user buys nobody anything and lands a web server's worth of
modules in the site-packages of someone who wanted to merge two PDFs.

``pyproject.toml`` excludes it from package discovery. Two things have to hold
for that exclusion to be safe rather than merely stated, and both are asserted
here: the exclusion is actually configured, and nothing that *does* ship imports
the package that does not. The second is the one that would bite — an import
added in a moment of convenience turns a green test suite into an ImportError
on a user's machine, where no test runs.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from tests.paths import REPO_ROOT, SRC, all_sources, relative

#: The package that is deliberately absent from the distribution.
EXCLUDED_PACKAGE = "docmax.server"


def _shipped_sources() -> list[Path]:
    """Every source file that ends up in the wheel."""
    server_root = SRC / "server"
    return [path for path in all_sources() if server_root not in path.parents]


def test_packaging_excludes_the_server() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    find = pyproject["tool"]["setuptools"]["packages"]["find"]

    excluded = find.get("exclude", [])
    assert any(pattern.startswith(EXCLUDED_PACKAGE) for pattern in excluded), (
        f"{EXCLUDED_PACKAGE} must stay out of the distribution: {excluded}"
    )


@pytest.mark.parametrize("source", _shipped_sources(), ids=relative)
def test_shipped_code_never_imports_the_server(source: Path) -> None:
    """Anything the wheel contains must run without the package it does not."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))

    offences: list[str] = []
    for node in ast.walk(tree):
        imported: list[str] = []
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            imported = [node.module]

        offences.extend(
            f"{relative(source)}:{node.lineno}: imports {name}"
            for name in imported
            if name == EXCLUDED_PACKAGE or name.startswith(f"{EXCLUDED_PACKAGE}.")
        )

    assert not offences, (
        f"{EXCLUDED_PACKAGE} is not in the wheel, so shipped code cannot import it.\n  "
        + "\n  ".join(offences)
    )
