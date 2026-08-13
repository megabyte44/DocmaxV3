"""Product identity lives in exactly one module.

``core/branding.py`` holds every brand literal. Everything else imports from it.
That keeps a rename, a new cloud domain, or a change of display name to a single
file — and, more importantly, makes a *partial* rename impossible to merge,
because this test fails on the leftovers.

The check is AST-based rather than a plain grep, which matters:

* ``from docmax.core import ...`` is an import, not a literal. The import name
  is the brand and always will be; banning it would ban the package.
* Docstrings legitimately say "DocMax" in prose.
* ``__all__`` must spell out identifiers like ``DocMaxError`` verbatim.

So only *user-facing string literals* are policed — which is exactly the set
that would need editing during a rename.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from tests.paths import REPO_ROOT, all_sources, relative

BRAND_FRAGMENTS = ("docmax",)

#: The one module allowed to contain brand literals.
EXEMPT = frozenset({"core/branding.py"})


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """Ids of Constant nodes that are docstrings, which are exempt."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _dunder_all_nodes(tree: ast.Module) -> set[int]:
    """Ids of Constant nodes inside an ``__all__`` assignment.

    These are identifiers being re-exported, not prose. A rename would update
    them via the class definitions, not by editing strings.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            continue
        ids.update(
            id(element)
            for element in ast.walk(node.value)
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        )
    return ids


@pytest.mark.parametrize("source", all_sources(), ids=relative)
def test_brand_literals_only_in_branding(source: Path) -> None:
    if relative(source).replace("\\", "/").endswith(tuple(EXEMPT)):
        pytest.skip("branding.py is the single source of truth")

    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    exempt_nodes = _docstring_nodes(tree) | _dunder_all_nodes(tree)

    offences = [
        f"{relative(source)}:{node.lineno}: {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in exempt_nodes
        and any(fragment in node.value.lower() for fragment in BRAND_FRAGMENTS)
    ]

    assert not offences, (
        "Brand literals belong in core/branding.py. Import APP_NAME / CLI_NAME / "
        "DIST_NAME from there instead.\n  " + "\n  ".join(offences)
    )


def test_pyproject_name_matches_branding() -> None:
    """The packaging metadata and the runtime constant must agree.

    If they drift, ``importlib.metadata.version(DIST_NAME)`` raises
    ``PackageNotFoundError`` at runtime and ``--version`` quietly reports
    ``0.0.0.dev0`` on a real install.
    """
    from docmax.core.branding import CLI_NAME, DIST_NAME

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["name"] == DIST_NAME
    assert CLI_NAME in pyproject["project"]["scripts"], (
        f"console script {CLI_NAME!r} missing from [project.scripts]"
    )
