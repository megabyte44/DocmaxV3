"""The architectural claims M9 makes, held by something other than review.

ADR 0023 says the runners import ``docmax.core`` and nothing else in the
project. The layers contract cannot say that — a layers contract permits every
layer below, so it allows ``runners -> tools`` while this ADR forbids it. The
gap is closed here, by reading the imports.

The two frozensets in ``pipeline.py`` are the other thing worth holding. They
are the fifth and third appearances of the same ``ToolSpec`` seam, and a
hardcoded list of tool names rots the moment a tool is renamed. Nothing can
check the *reverse* direction — see the note on that test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from docmax.core.registry import build_registry
from docmax.runners.pipeline import NOT_A_MIDDLE_STAGE, SUFFIX_FROM_PARAMS
from tests.paths import SRC, relative

RUNNERS = SRC / "runners"

#: What a runner may import from this project. ``core`` is the contract layer;
#: ``runners`` is itself, since the three modules compose one another.
PERMITTED_PREFIXES = ("docmax.core", "docmax.runners")


def runner_sources() -> list[Path]:
    return sorted(RUNNERS.rglob("*.py"))


def imported_docmax_modules(source: Path) -> list[str]:
    """Every ``docmax.*`` module this file imports, at any indentation."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if alias.name.startswith("docmax"))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("docmax"):
            found.append(node.module)

    return found


@pytest.mark.parametrize("source", runner_sources(), ids=relative)
def test_runners_import_only_core(source: Path) -> None:
    """ADR 0023's load-bearing claim, and the one the layers contract cannot make.

    A runner names a tool by string and lets the registry resolve it. Reaching
    into ``docmax.tools`` would work, would pass ``lint-imports``, and would
    quietly make the runners depend on pypdf.
    """
    offences = [
        name for name in imported_docmax_modules(source) if not name.startswith(PERMITTED_PREFIXES)
    ]

    assert not offences, (
        f"{relative(source)} imports {offences}.\n"
        "The runners compose tools through the registry and the router; they may "
        "not import a tool, an interface, or the cloud client. See ADR 0023."
    )


def test_the_runners_package_exists_as_its_own_layer() -> None:
    """The contract line is what makes the layer real rather than a directory."""
    contract = (SRC.parent.parent / ".importlinter").read_text(encoding="utf-8")

    assert "docmax.runners" in contract


def test_core_may_not_import_the_runners() -> None:
    """ "M9 needed no Core change" is only a fact if Core cannot reach M9."""
    contract = (SRC.parent.parent / ".importlinter").read_text(encoding="utf-8")
    section = contract.partition("[importlinter:contract:core-is-standalone]")[2]

    assert "docmax.runners" in section.partition("[importlinter")[0]


@pytest.mark.parametrize("name", sorted(NOT_A_MIDDLE_STAGE | SUFFIX_FROM_PARAMS))
def test_every_named_tool_is_a_real_tool(name: str) -> None:
    """A hardcoded tool name that no longer exists is a rule that silently stopped.

    This checks the direction that *can* be checked. The reverse — "a newly added
    tool that produces a directory is in the set" — is exactly what ``ToolSpec``
    cannot express, which is the seam ADR 0024 records rather than closes. A new
    directory-producing tool will pass this suite and be wrong, and only the
    pipeline's runtime failure will say so.
    """
    assert name in build_registry()


def test_the_runners_never_reach_for_a_console() -> None:
    """Library code reports through the sink it was given, not by printing."""
    forbidden = ("rich", "typer", "textual")
    offences: list[str] = []

    for source in runner_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offences += [
                    f"{relative(source)}: {alias.name}"
                    for alias in node.names
                    if alias.name.split(".")[0] in forbidden
                ]
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.split(".")[0] in forbidden
            ):
                offences.append(f"{relative(source)}: {node.module}")

    assert not offences, offences


def test_the_runners_are_covered_by_the_hygiene_suite() -> None:
    """Being in LIBRARY_PACKAGES is what enrols them in no-writes and no-exit."""
    from tests.paths import LIBRARY_PACKAGES

    assert "runners" in LIBRARY_PACKAGES
