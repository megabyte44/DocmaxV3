"""Library code must never terminate the process.

This is the structural fix for v2's single worst design flaw. v2 had one
``abort()`` helper — print, then ``sys.exit(1)`` — called from 64 sites inside
operation modules. Two things followed:

* The modules could not be used as a library. Importing ``docmax.operations``
  and calling ``merge()`` might kill your process.
* ``SystemExit`` does not inherit from ``Exception``, so every
  ``except Exception`` handler in the batch runner, the folder watcher, and the
  interactive workflows silently failed to catch it. A single missing
  dependency terminated an entire 200-file batch mid-run.

Library code raises typed errors. Only ``docmax.cli`` decides to exit.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.paths import library_sources, relative

#: ``foo()`` where foo is one of these.
FORBIDDEN_NAMES = frozenset({"exit", "quit"})

#: ``mod.attr()`` where (mod, attr) is one of these.
FORBIDDEN_ATTRS = frozenset(
    {
        ("sys", "exit"),
        ("os", "_exit"),
        ("os", "abort"),
        ("typer", "Exit"),
        ("typer", "Abort"),
    }
)


def _describe_exit(node: ast.AST) -> str | None:
    """Return a description if this node terminates the process, else None."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
            return f"{func.id}()"
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and (func.value.id, func.attr) in FORBIDDEN_ATTRS
        ):
            return f"{func.value.id}.{func.attr}()"

    if isinstance(node, ast.Raise) and node.exc is not None:
        exc = node.exc
        target = exc.func if isinstance(exc, ast.Call) else exc
        if isinstance(target, ast.Name) and target.id == "SystemExit":
            return "raise SystemExit"
        if isinstance(target, ast.Attribute) and target.attr in {"Exit", "Abort"}:
            return f"raise ...{target.attr}"

    return None


@pytest.mark.parametrize("source", library_sources(), ids=relative)
def test_library_code_never_exits_the_process(source: Path) -> None:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))

    offences: list[str] = []
    for node in ast.walk(tree):
        description = _describe_exit(node)
        if description is not None:
            line: int = getattr(node, "lineno", 0)
            offences.append(f"{relative(source)}:{line}: {description}")

    assert not offences, (
        "Library code must raise a typed error, not terminate the process.\n"
        "Only docmax.cli may exit. See docmax/core/errors.py.\n  " + "\n  ".join(offences)
    )
