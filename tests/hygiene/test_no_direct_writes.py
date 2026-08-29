"""Every write goes through the atomic helpers.

v2 had no atomic write anywhere. Output was written straight to the destination
with ``open(out, "wb")``, ``write_text``, or ``cv2.imwrite``. Three consequences
shipped to users:

* A crash or Ctrl-C mid-write left a truncated, unopenable file where their
  document used to be.
* Re-running any command silently destroyed the previous run's output.
* Several code paths wrote *over the input itself* — ``convert x.md --to md``
  destroyed the source outright.

``core/atomic.py`` writes to a temp file on the same filesystem, runs the
operation's validators against it, and only then ``os.replace()``s it into
place. This test makes bypassing that mechanism a build failure rather than a
code-review question.

The helpers themselves are the sole exemption — see ``EXEMPT``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.paths import library_sources, relative

#: The only modules allowed to call a write directly, and why.
#:
#: ``core/atomic.py`` is the mechanism itself.
#:
#: ``tools/_deskew.py`` (M8) is the first library code to write a *scratch*
#: file: it rotates a rasterised page in place, and the path it is handed is
#: always inside a ``TemporaryDirectory`` that ``ocr/local.py`` created and
#: removes. Every other tool avoids appearing here only because its intermediate
#: writes happen inside a subprocess — Ghostscript writing a staged file is the
#: same act, invisible to an AST scan.
#:
#: The rule this test exists for is unweakened: a *destination* is still written
#: only by ``core/atomic.py``. What is exempted is a file that cannot be a
#: user's, and `test_deskew.py::test_straighten_writes_only_the_file_it_was_given`
#: asserts that at run time — which the AST cannot. See ADR 0022.
EXEMPT = frozenset({"core/atomic.py", "tools/_deskew.py"})

#: ``path.write_text(...)`` / ``path.write_bytes(...)`` and friends.
FORBIDDEN_METHODS = frozenset({"write_text", "write_bytes"})

#: ``cv2.imwrite(...)``, ``img.save(...)`` — libraries that write by side effect.
FORBIDDEN_ATTRS = frozenset({("cv2", "imwrite"), ("shutil", "move"), ("shutil", "copy")})

#: Modes that create or truncate.
WRITE_MODES = ("w", "a", "x", "+")


def _is_write_open(node: ast.Call) -> bool:
    """True for ``open(..., "w")`` and any other creating/truncating mode."""
    func = node.func
    is_open = (isinstance(func, ast.Name) and func.id == "open") or (
        isinstance(func, ast.Attribute) and func.attr == "open"
    )
    if not is_open:
        return False

    mode: str | None = None
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        value = node.args[1].value
        mode = value if isinstance(value, str) else None
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            mode = value if isinstance(value, str) else None

    if mode is None:
        return False  # defaults to "r"
    return any(flag in mode for flag in WRITE_MODES)


def _describe_write(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None

    if _is_write_open(node):
        return "open(..., write mode)"

    func = node.func
    if isinstance(func, ast.Attribute):
        if func.attr in FORBIDDEN_METHODS:
            return f".{func.attr}()"
        if isinstance(func.value, ast.Name) and (func.value.id, func.attr) in FORBIDDEN_ATTRS:
            return f"{func.value.id}.{func.attr}()"

    return None


@pytest.mark.parametrize("source", library_sources(), ids=relative)
def test_no_direct_writes_outside_atomic(source: Path) -> None:
    if relative(source).replace("\\", "/").endswith(tuple(EXEMPT)):
        pytest.skip("exempt: see EXEMPT for the reason this module may write")

    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))

    offences: list[str] = []
    for node in ast.walk(tree):
        description = _describe_write(node)
        if description is not None:
            line: int = getattr(node, "lineno", 0)
            offences.append(f"{relative(source)}:{line}: {description}")

    assert not offences, (
        "Writes must go through core/atomic.py (atomic_write / atomic_path / atomic_dir)\n"
        "so that a crash mid-operation can never corrupt a user's file.\n  " + "\n  ".join(offences)
    )
